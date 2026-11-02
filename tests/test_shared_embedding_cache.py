"""统一缓存真实行为验证；不访问百炼、不读取生产记忆。"""

import asyncio
import base64
import sqlite3
import struct
from types import SimpleNamespace

import aiohttp
import pytest

from astrbot_plugin_livingmemory.core.embedding_cache import EmbeddingCache
from astrbot_plugin_livingmemory.core.embedding_gateway import (
    SharedEmbeddingGateway, UpstreamEmbeddingError,
)
from astrbot_plugin_livingmemory.core.validators.index_validator import IndexValidator


async def make_cache(tmp_path, **kwargs):
    calls = []

    async def fetch(texts):
        calls.append(list(texts))
        await asyncio.sleep(0)
        return [[float(len(t)), 0.123456789012345] for t in texts], 7 * len(texts)

    cache = EmbeddingCache(str(tmp_path / "cache.db"), "model-v4", 2, fetch, **kwargs)
    await cache.start()
    return cache, calls


@pytest.mark.asyncio
async def test_duplicate_inputs_order_and_paid_tokens(tmp_path):
    cache, calls = await make_cache(tmp_path)
    try:
        vectors, tokens, _ = await cache.get(["a", "bb", "a"])
        assert calls == [["a", "bb"]]
        assert vectors[0] == vectors[2]
        assert tokens == 14
        again, tokens, info = await cache.get(["bb", "a"])
        assert again == [vectors[1], vectors[0]]
        assert tokens == 0
        assert info["hits"] == 2
        assert len(calls) == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_parallel_requests_coalesce(tmp_path):
    cache, calls = await make_cache(tmp_path)
    try:
        outputs = await asyncio.gather(*(cache.get(["same"]) for _ in range(20)))
        assert calls == [["same"]]
        assert sum(out[1] for out in outputs) == 7
        assert all(out[0] == outputs[0][0] for out in outputs)
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_paid_work(tmp_path):
    cache, calls = await make_cache(tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow(texts):
        calls.append(texts)
        started.set()
        await finish.wait()
        return [[1.0, 2.0]], 7

    cache.fetch = slow
    try:
        first = asyncio.create_task(cache.get(["a"]))
        await started.wait()
        second = asyncio.create_task(cache.get(["a"]))
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        finish.set()
        assert (await second)[0] == [[1.0, 2.0]]
        assert (await cache.get(["a"]))[1] == 0
        assert len(calls) == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_restart_reuses_vectors_without_precision_change(tmp_path):
    cache, calls = await make_cache(tmp_path)
    original = (await cache.get(["persist"]))[0]
    await cache.close()
    cache, calls = await make_cache(tmp_path)
    try:
        assert (await cache.get(["persist"]))[0] == original
        assert calls == []
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_namespace_isolation(tmp_path):
    cache, _ = await make_cache(tmp_path)
    await cache.get(["a"])
    await cache.close()
    cache, calls = await make_cache(tmp_path)
    cache.namespace = "different-model-space"
    try:
        await cache.get(["a"])
        assert calls == [["a"]]
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_expired_and_corrupt_rows_recompute(tmp_path):
    cache, calls = await make_cache(tmp_path)
    try:
        await cache.get(["a", "bb"])
        with sqlite3.connect(cache.path) as db:
            db.execute("UPDATE embeddings SET created_at=0 WHERE cache_key=?", (cache.key("a"),))
            db.execute("UPDATE embeddings SET vector=X'00' WHERE cache_key=?", (cache.key("bb"),))
        cache._recent.clear()
        await cache.get(["a", "bb"])
        assert len(calls) == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [[], [float("nan"), 1], [1], [float("inf"), 1]])
async def test_invalid_upstream_vector_not_cached(tmp_path, bad):
    cache, calls = await make_cache(tmp_path)
    async def fetch(texts):
        calls.append(texts)
        return [bad], 7
    cache.fetch = fetch
    try:
        for _ in range(2):
            with pytest.raises(ValueError):
                await cache.get(["a"])
        assert len(calls) == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_partial_batch_retains_paid_success(tmp_path):
    cache, calls = await make_cache(tmp_path)
    async def fetch(texts):
        calls.append(texts)
        if len(calls) == 2:
            raise UpstreamEmbeddingError(429)
        return [[1.0, 2.0] for _ in texts], 7 * len(texts)
    cache.fetch = fetch
    try:
        with pytest.raises(UpstreamEmbeddingError):
            await cache.get([str(i) for i in range(11)])
        assert len(calls[0]) == 10
        _, tokens, info = await cache.get([str(i) for i in range(10)])
        assert tokens == 0 and info["hits"] == 10
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_cache_size_is_bounded(tmp_path):
    cache, _ = await make_cache(tmp_path, max_entries=2)
    try:
        await cache.get(["a", "b", "c"])
        with sqlite3.connect(cache.path) as db:
            assert db.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 2
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_disk_write_failure_keeps_successful_vector_in_memory(tmp_path):
    cache, calls = await make_cache(tmp_path)
    def fail_write(*args):
        raise sqlite3.OperationalError("test disk failure")
    cache._write = fail_write
    try:
        await cache.get(["paid"])
        assert (await cache.get(["paid"]))[1] == 0
        assert calls == [["paid"]]
    finally:
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("texts", [None, "a", [123], [""], ["a"] * 129])
async def test_input_validation_precedes_upstream(tmp_path, texts):
    cache, calls = await make_cache(tmp_path)
    try:
        with pytest.raises(ValueError):
            await cache.get(texts)
        assert calls == []
    finally:
        await cache.close()


class Provider:
    api_key = "test-only-upstream-key"
    request_headers = {}
    model = "text-embedding-v4"
    provider_config = {
        "id": "dashscope_embedding", "type": "dashscope_embedding",
        "embedding_model": model, "embedding_dimensions": 64,
        "embedding_api_base": "https://dashscope.aliyuncs.com/api/v1", "timeout": 10,
    }

    def get_dim(self):
        return self.provider_config["embedding_dimensions"]

    def get_model(self):
        return self.model

    async def get_embedding(self, text):
        return (await self.get_embeddings([text]))[0]

    async def get_embeddings(self, texts):
        return [[9.0] * 64 for _ in texts]


async def make_gateway(tmp_path):
    provider = Provider()
    context = SimpleNamespace(get_all_embedding_providers=lambda: [provider])
    gateway = SharedEmbeddingGateway(context, {"port": 0}, str(tmp_path))
    calls = []
    async def fetch(texts):
        calls.append(list(texts))
        return [[0.25] * 64 for _ in texts], 3 * len(texts)
    gateway._fetch = fetch
    await gateway.start_for(provider)
    port = gateway.runner.addresses[0][1]
    return gateway, provider, calls, f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_provider_and_http_share_cache_and_fingerprint(tmp_path):
    provider = Provider()
    validator = IndexValidator("unused.db", SimpleNamespace(embedding_provider=provider))
    before = validator.get_provider_fingerprint()
    gateway = SharedEmbeddingGateway(
        SimpleNamespace(get_all_embedding_providers=lambda: [provider]),
        {"port": 0}, str(tmp_path),
    )
    calls = []
    async def fetch(texts):
        calls.append(texts)
        return [[0.25] * 64 for _ in texts], 3 * len(texts)
    gateway._fetch = fetch
    original = provider.get_embedding
    await gateway.start_for(provider)
    try:
        assert validator.get_provider_fingerprint() == before
        assert await provider.get_embedding("shared") == [0.25] * 64
        port = gateway.runner.addresses[0][1]
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{port}/v1/embeddings",
                headers={"Authorization": f"Bearer {gateway.token}"},
                json={"model": provider.model, "input": ["shared"], "encoding_format": "base64"},
            ) as response:
                assert response.status == 200
                body = await response.json()
                assert body["usage"]["total_tokens"] == 0
                assert struct.unpack("<64f", base64.b64decode(body["data"][0]["embedding"])) == (0.25,) * 64
        assert len(calls) == 1
    finally:
        await gateway.close()
    assert provider.get_embedding == original
    assert validator.get_provider_fingerprint() == before


@pytest.mark.asyncio
async def test_http_auth_and_dimension_rejection(tmp_path):
    gateway, provider, calls, url = await make_gateway(tmp_path)
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(url + "/v1/embeddings", json={"input": "a"}) as response:
                assert response.status == 401
            async with client.post(
                url + "/v1/embeddings",
                headers={"Authorization": f"Bearer {gateway.token}"},
                json={"model": provider.model, "input": "a", "dimensions": 1024},
            ) as response:
                assert response.status == 400
            async with client.get(url + "/metrics") as response:
                assert response.status == 401
        assert calls == []
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_token_persists_and_is_not_world_readable(tmp_path):
    gateway, _, _, _ = await make_gateway(tmp_path)
    token = gateway.token
    await gateway.close()
    assert (tmp_path / "embedding_gateway.token").stat().st_mode & 0o077 == 0
    gateway, _, _, _ = await make_gateway(tmp_path)
    try:
        assert gateway.token == token
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_provider_mutation_cannot_reuse_old_space(tmp_path):
    gateway, provider, _, _ = await make_gateway(tmp_path)
    try:
        await provider.get_embedding("cached")
        provider.provider_config = dict(provider.provider_config, embedding_dimensions=128)
        with pytest.raises(RuntimeError, match="Provider changed"):
            await provider.get_embedding("cached")
    finally:
        await gateway.close()
