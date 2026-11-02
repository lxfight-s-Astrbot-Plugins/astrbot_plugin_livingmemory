"""LivingMemory 承载公共向量入口；原有提供商指纹和记忆权限保持独立。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import os
import secrets
import struct
from pathlib import Path

import aiohttp
from aiohttp import web
from astrbot.api import logger

from .embedding_cache import EmbeddingCache


class UpstreamEmbeddingError(RuntimeError):
    def __init__(self, status: int):
        self.status = status if status in (400, 401, 403, 429) else 502
        super().__init__(f"Embedding upstream returned HTTP {status}")


class SharedEmbeddingGateway:
    def __init__(self, context, config: dict, data_dir: str):
        self.context = context
        self.config = config
        self.data_dir = Path(data_dir)
        self.cache: EmbeddingCache | None = None
        self.session: aiohttp.ClientSession | None = None
        self.runner: web.AppRunner | None = None
        self._provider = None
        self._wrapped = {}
        self._watcher = None
        self._start_lock = asyncio.Lock()
        self._closed = False
        self.callers: dict[str, dict] = {}
        self.token = ""

    def fingerprint(self, provider):
        cfg = provider.provider_config
        if cfg.get("type") != "dashscope_embedding":
            raise ValueError("Shared cache currently requires DashScope native provider")
        model = cfg.get("embedding_model", "")
        if model != "text-embedding-v4":
            raise ValueError("Shared cache currently supports text-embedding-v4 only")
        dimension = provider.get_dim()
        if dimension not in (64, 128, 256, 512, 768, 1024, 1536, 2048):
            raise ValueError("Invalid configured embedding dimension")
        api_key = getattr(provider, "api_key", "")
        if not api_key:
            raise ValueError("Embedding provider has no API key")
        identity = [
            cfg.get("embedding_api_base", "").rstrip("/"), model, dimension,
            hashlib.sha256(api_key.encode()).hexdigest(),
            self.config.get("revision", "v4-cache-1"), "native-default-input",
        ]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    async def start_for(self, provider):
        if self._closed:
            raise RuntimeError("Shared embedding gateway stopping")
        async with self._start_lock:
            namespace = self.fingerprint(provider)
            if self.cache is not None:
                if namespace != self.cache.namespace:
                    raise ValueError("Provider changed; reload LivingMemory to reopen cache")
                self._wrap(provider)
                return
            self._provider = provider
            token_path = self.data_dir / "embedding_gateway.token"
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if token_path.exists():
                self.token = token_path.read_text().strip()
            else:
                # 文件不进入源码和日志；调用方使用独立本机令牌，不需要百炼密钥。
                self.token = secrets.token_urlsafe(32)
                fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(self.token)
            if len(self.token) < 32:
                raise ValueError("Invalid embedding gateway token")
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=float(provider.provider_config.get("timeout", 60))
                )
            )
            cache = EmbeddingCache(
                str(self.data_dir / "embedding_cache.db"),
                namespace, provider.get_dim(), self._fetch,
                ttl_days=self.config.get("ttl_days", 90),
                max_entries=self.config.get("max_entries", 10000),
                concurrency=self.config.get("concurrency", 2),
            )
            runner = None
            self.session = session
            try:
                await cache.start()
                app = web.Application(client_max_size=1024 * 1024)
                app.router.add_post("/v1/embeddings", self._handle_embeddings)
                app.router.add_get("/health", self._health)
                app.router.add_get("/metrics", self._metrics)
                runner = web.AppRunner(app, access_log=None)
                await runner.setup()
                # 仅监听本机，不复用 Dashboard 的访问令牌，也不开放公网转发。
                await web.TCPSite(
                    runner, "127.0.0.1", self.config.get("port", 17695)
                ).start()
            except BaseException:
                if runner is not None:
                    await runner.cleanup()
                await cache.close()
                await session.close()
                self.session = None
                raise
            self.cache, self.runner = cache, runner
            self._wrap(provider)
            self._watcher = asyncio.create_task(self._watch_providers())
            logger.info(
                "[LivingMemory] Shared embedding cache ready on localhost:%s",
                self.config.get("port", 17695),
            )

    async def _fetch(self, texts: list[str]):
        provider = self._provider
        if self.fingerprint(provider) != self.cache.namespace:
            raise RuntimeError("Provider changed; reload LivingMemory")
        cfg = provider.provider_config
        base = cfg["embedding_api_base"].rstrip("/")
        headers = dict(getattr(provider, "request_headers", {}) or {})
        headers["Authorization"] = f"Bearer {provider.api_key}"
        payload = {
            "model": cfg["embedding_model"],
            "input": {"texts": texts},
            "parameters": {"dimension": provider.get_dim()},
        }
        assert self.session is not None
        # 直接连接百炼，绝不通过已包装的 get_embeddings 绕回自身。
        async with self.session.post(
            base + "/services/embeddings/text-embedding/text-embedding",
            headers=headers, json=payload, proxy=cfg.get("proxy") or None,
        ) as response:
            if response.status != 200:
                raise UpstreamEmbeddingError(response.status)
            data = await response.json()
        rows = data.get("output", {}).get("embeddings", [])
        if sorted(row.get("text_index") for row in rows) != list(range(len(texts))):
            raise ValueError("Embedding response indexes do not match inputs")
        vectors = [row["embedding"] for row in sorted(rows, key=lambda r: r["text_index"])]
        usage = data.get("usage", {})
        if "total_tokens" not in usage:
            raise ValueError("Embedding upstream did not return token usage")
        return vectors, max(0, int(usage["total_tokens"]))

    @staticmethod
    def _caller():
        frame = inspect.currentframe()
        try:
            for _ in range(24):
                if frame is None:
                    break
                parts = Path(frame.f_code.co_filename).parts
                if frame.f_code.co_filename != __file__ and "plugins" in parts:
                    index = parts.index("plugins") + 1
                    if index < len(parts):
                        return parts[index]
                frame = frame.f_back
        finally:
            del frame
        return "astrbot-provider"

    def _record(self, caller, info, tokens):
        caller = str(caller)[:64]
        if caller not in self.callers and len(self.callers) >= 128:
            caller = "other"
        stats = self.callers.setdefault(caller, dict(requests=0, hits=0, coalesced=0, tokens=0))
        stats["requests"] += 1
        stats["hits"] += info["hits"]
        stats["coalesced"] += info["coalesced"]
        stats["tokens"] += tokens

    def _wrap(self, provider):
        if id(provider) in self._wrapped:
            return
        if getattr(provider, "_livingmemory_cache_owner", None) is not None:
            return
        original_one, original_many = provider.get_embedding, provider.get_embeddings

        async def get_many(texts):
            assert self.cache is not None
            if self.fingerprint(provider) != self.cache.namespace:
                raise RuntimeError("Provider changed; reload LivingMemory")
            vectors, tokens, info = await self.cache.get(texts)
            self._record(self._caller(), info, tokens)
            return vectors

        async def get_one(text):
            values = await get_many([text])
            return values[0]

        provider.get_embedding, provider.get_embeddings = get_one, get_many
        provider._livingmemory_cache_owner = self
        self._wrapped[id(provider)] = (provider, original_one, original_many, get_one, get_many)

    async def _watch_providers(self):
        while not self._closed:
            try:
                for provider in self.context.get_all_embedding_providers():
                    try:
                        if self.fingerprint(provider) == self.cache.namespace:
                            self._wrap(provider)
                    except (AttributeError, ValueError, TypeError):
                        continue
            except Exception:
                logger.warning("[LivingMemory] Shared provider discovery failed", exc_info=True)
            await asyncio.sleep(2)

    def _authorized(self, request):
        return hmac.compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {self.token}"
        )

    async def _handle_embeddings(self, request):
        if not self._authorized(request):
            return web.json_response({"error": {"message": "Unauthorized"}}, status=401)
        if self.cache is None or self._closed:
            return web.json_response({"error": {"message": "Cache unavailable"}}, status=503)
        try:
            if self.fingerprint(self._provider) != self.cache.namespace:
                raise RuntimeError("Provider changed; reload LivingMemory")
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            if body.get("model") != "text-embedding-v4":
                raise ValueError("Unsupported model")
            dim = body.get("dimensions", self.cache.dimension)
            if type(dim) is not int or dim != self.cache.dimension:
                raise ValueError("Dimensions must match configured provider")
            fmt = body.get("encoding_format", "float")
            if fmt not in ("float", "base64"):
                raise ValueError("Unsupported encoding format")
            texts = body.get("input")
            if isinstance(texts, str):
                texts = [texts]
            vectors, tokens, info = await self.cache.get(texts)
            self._record(request.headers.get("X-Embedding-Caller", "http-client"), info, tokens)
            if fmt == "base64":
                vectors = [
                    base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode()
                    for v in vectors
                ]
            return web.json_response(
                {
                    "object": "list", "model": "text-embedding-v4",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": v}
                        for i, v in enumerate(vectors)
                    ],
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                },
                headers={"X-Embedding-Cache": json.dumps(info)},
            )
        except UpstreamEmbeddingError as exc:
            return web.json_response({"error": {"message": str(exc)}}, status=exc.status)
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": {"message": "Invalid embedding input or response"}}, status=400)
        except Exception:
            logger.warning("[LivingMemory] Shared embedding request failed", exc_info=True)
            return web.json_response({"error": {"message": "Embedding unavailable"}}, status=503)

    async def _health(self, request):
        ready = self.cache is not None and not self._closed
        return web.json_response(
            {"ready": ready, "model": "text-embedding-v4",
             "dimensions": self.cache.dimension if self.cache else None},
            status=200 if ready else 503,
        )

    async def _metrics(self, request):
        if not self._authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        return web.json_response(
            {"since_start": dict(self.cache.stats), "by_caller": self.callers}
        )

    async def close(self):
        self._closed = True
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
        for provider, one, many, wrapped_one, wrapped_many in self._wrapped.values():
            if getattr(provider, "_livingmemory_cache_owner", None) is self:
                if provider.get_embedding is wrapped_one:
                    provider.get_embedding = one
                if provider.get_embeddings is wrapped_many:
                    provider.get_embeddings = many
                del provider._livingmemory_cache_owner
        if self.runner is not None:
            await self.runner.cleanup()
        if self.cache is not None:
            await self.cache.close()
        if self.session is not None:
            await self.session.close()
