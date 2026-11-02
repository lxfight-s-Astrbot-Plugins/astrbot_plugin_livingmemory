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
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from astrbot.api import logger

from .embedding_cache import EmbeddingCache


class UpstreamEmbeddingError(RuntimeError):
    def __init__(self, status: int):
        self.status = status if status in (400, 401, 403, 429) else 502
        super().__init__(f"Embedding upstream returned HTTP {status}")


class EmbeddingInputError(ValueError):
    """仅用于可直接展示给调用方的本机输入校验错误。"""


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

    @staticmethod
    def _openai_options(provider):
        # 复用 AstrBot 的维度发送规则；直接用 SDK，不能递归调用已包装的方法。
        options = getattr(provider, "_embedding_kwargs", None)
        if callable(options):
            return dict(options())
        cfg = provider.provider_config
        mode = cfg.get("embedding_dimensions_mode", "auto")
        model = cfg.get("embedding_model", "").lower().rsplit("/", 1)[-1]
        base = urlsplit(str(provider.client.base_url))
        if mode not in ("auto", "always", "never"):
            mode = "auto"
        if mode == "always" or (
            mode == "auto" and (
                (base.scheme == "https" and base.hostname == "api.openai.com"
                 and base.path.rstrip("/") == "/v1" and model.startswith("text-embedding-3"))
                or (base.scheme == "https" and base.hostname == "api.siliconflow.cn"
                    and model.startswith("qwen"))
            )
        ):
            return {"dimensions": provider.get_dim()}
        return {}

    def _settings(self, provider):
        cfg = provider.provider_config
        protocol = cfg.get("type")
        if protocol not in ("dashscope_embedding", "openai_embedding"):
            raise ValueError(
                "Shared cache requires DashScope text embedding or OpenAI Embedding; "
                "configure an OpenAI-compatible embedding provider in AstrBot first"
            )
        model = cfg.get("embedding_model", "")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Fill in the embedding model name in the AstrBot provider")
        if protocol == "dashscope_embedding" and not model.startswith("text-embedding-"):
            raise ValueError("DashScope shared cache supports text-embedding-* text models only")
        dimension = provider.get_dim()
        if type(dimension) is not int or not 1 <= dimension <= 16384:
            raise ValueError("Fill in the actual output dimensions (1–16384) in AstrBot")
        configured_model = self.config.get("model_name", "").strip()
        configured_dimension = self.config.get("dimensions", 0)
        batch_size = self.config.get("upstream_batch_size", 10)
        if type(batch_size) is not int or not 1 <= batch_size <= 128:
            raise ValueError("upstream_batch_size must be between 1 and 128")
        if type(configured_dimension) is not int or not 0 <= configured_dimension <= 16384:
            raise ValueError("Cache dimensions must be 0 (native v4 only) or an integer from 1 to 16384")
        if not (protocol == "dashscope_embedding" and model == "text-embedding-v4"):
            if not configured_model or not configured_dimension:
                raise ValueError(
                    "For other embedding models, fill shared_embedding_cache.model_name "
                    "and dimensions from the AstrBot provider (e.g. BAAI/bge-m3, 1024)"
                )
        if configured_model and configured_model != model:
            raise ValueError("Cache model_name must match the AstrBot embedding model name, not Provider ID")
        if configured_dimension and (
            type(configured_dimension) is not int or configured_dimension != dimension
        ):
            raise ValueError("Cache dimensions must match the AstrBot provider's actual output dimensions")
        if protocol == "dashscope_embedding":
            if model == "text-embedding-v4" and dimension not in (64, 128, 256, 512, 768, 1024, 1536, 2048):
                raise ValueError("Invalid text-embedding-v4 dimensions; consult the model documentation")
            api_key = getattr(provider, "api_key", "")
            base = cfg.get("embedding_api_base", "").rstrip("/")
        else:
            api_key = getattr(provider.client, "api_key", "")
            base = str(provider.client.base_url).rstrip("/")
        if not api_key:
            raise ValueError("Embedding provider has no API key")
        parsed = urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Fill in a valid upstream embedding API Base URL in AstrBot")
        own_ports = {self.config.get("port", 17695)}
        if self.runner is not None:
            own_ports.update(address[1] for address in self.runner.addresses)
        if parsed.hostname.lower() in ("localhost", "127.0.0.1", "::1") and parsed.port in own_ports:
            raise ValueError("The upstream provider must not point to LivingMemory's own cache endpoint")
        return protocol, model, dimension, api_key, base

    def fingerprint(self, provider):
        protocol, model, dimension, api_key, base = self._settings(provider)
        identity = [
            base, model, dimension,
            hashlib.sha256(api_key.encode()).hexdigest(),
            self.config.get("revision", "v4-cache-1"), "native-default-input",
        ]
        # 原生 v4 的命名空间逐字兼容旧版，升级后不重新消耗已有缓存。
        if protocol == "openai_embedding":
            identity[-1] = ["openai-text-input", self._openai_options(provider)]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @property
    def model_name(self):
        return self._provider.provider_config["embedding_model"] if self._provider else None

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
                batch_size=min(self.config.get("upstream_batch_size", 10), 10)
                if provider.provider_config["type"] == "dashscope_embedding"
                else self.config.get("upstream_batch_size", 10),
            )
            runner = None
            self.session = session
            try:
                await cache.start()
                app = web.Application(client_max_size=1024 * 1024)
                app.router.add_post("/v1/embeddings", self._handle_embeddings)
                app.router.add_get("/v1/models", self._models)
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
        if cfg["type"] == "openai_embedding":
            try:
                result = await provider.client.with_options(max_retries=0).embeddings.create(
                    input=texts, model=self.model_name, encoding_format="float",
                    **self._openai_options(provider),
                )
            except Exception as exc:
                # SDK 错误正文可能含上游请求信息，仅传递状态码。
                raise UpstreamEmbeddingError(getattr(exc, "status_code", 502)) from None
            rows = result.data
            if sorted(row.index for row in rows) != list(range(len(texts))):
                raise ValueError("Embedding response indexes do not match inputs")
            vectors = [row.embedding for row in sorted(rows, key=lambda row: row.index)]
            return vectors, self._tokens(getattr(result, "usage", None))
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
        return vectors, self._tokens(data.get("usage"))

    @staticmethod
    def _tokens(usage):
        value = usage.get("total_tokens") if isinstance(usage, dict) else getattr(usage, "total_tokens", None)
        # 上游缺失计量时仍可缓存有效向量，不能把未知消耗显示为免费。
        return value if type(value) is int and value >= 0 else None

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
        stats = self.callers.setdefault(
            caller, dict(requests=0, hits=0, coalesced=0, tokens=0, unknown_requests=0)
        )
        stats["requests"] += 1
        stats["hits"] += info["hits"]
        stats["coalesced"] += info["coalesced"]
        if tokens is None:
            stats["unknown_requests"] += 1
        else:
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
                raise EmbeddingInputError("Expected a JSON object")
            if body.get("model") != self.model_name:
                raise EmbeddingInputError(f"Set model to the configured embedding model: {self.model_name}")
            dim = body.get("dimensions", self.cache.dimension)
            if type(dim) is not int or dim != self.cache.dimension:
                raise EmbeddingInputError(f"Set dimensions to {self.cache.dimension}, or omit it")
            fmt = body.get("encoding_format", "float")
            if fmt not in ("float", "base64"):
                raise EmbeddingInputError("encoding_format must be float or base64")
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
                    "object": "list", "model": self.model_name,
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
        except EmbeddingInputError as exc:
            return web.json_response({"error": {"message": str(exc)}}, status=400)
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": {"message": "Invalid embedding input or response"}}, status=400)
        except Exception:
            logger.warning("[LivingMemory] Shared embedding request failed", exc_info=True)
            return web.json_response({"error": {"message": "Embedding unavailable"}}, status=503)

    async def _health(self, request):
        ready = self._ready()
        return web.json_response(
            {"ready": ready, "model": self.model_name,
             "dimensions": self.cache.dimension if self.cache else None},
            status=200 if ready else 503,
        )

    async def _models(self, request):
        if not self._authorized(request):
            return web.json_response({"error": {"message": "Unauthorized"}}, status=401)
        if not self._ready():
            return web.json_response({"error": {"message": "Cache unavailable"}}, status=503)
        return web.json_response({
            "object": "list",
            "data": [{"id": self.model_name, "object": "model", "owned_by": "livingmemory"}],
        })

    def _ready(self):
        if self.cache is None or self._closed:
            return False
        try:
            return self.fingerprint(self._provider) == self.cache.namespace
        except (ValueError, TypeError, AttributeError):
            return False

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
