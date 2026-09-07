"""Shared query embeddings and bounded, scope-aware vector candidate expansion."""

import asyncio
import json
from contextvars import ContextVar
from types import SimpleNamespace

from ..faiss_async_persist import get_async_persister

query_embeddings = ContextVar("livingmemory_query_embeddings", default=None)


async def retrieve_vectors(db, query, k, filters, *, source_key="id"):
    """Retrieve unique active candidates without repeating the embedding request.

    Args:
        db: AstrBot vector database.
        query: Query text; the same budget applies to both vector routes.
        k: Maximum number of results.
        filters: Session and persona restrictions.
        source_key: Document field used to deduplicate graph source memories.

    Returns:
        Ranked vector hits. Candidate expansion is capped at 8192 vectors.
    """
    if k <= 0:
        return []
    query = query[:2000]
    storage = getattr(db, "embedding_storage", None)
    total = getattr(getattr(storage, "index", None), "ntotal", None)
    native = isinstance(total, int)
    cap = min(total, 8192) if native else 8192
    if native and not total:
        return []
    fetch_k = min(cap, max(k * (4 if filters else 2), k))

    if native:
        import numpy as np

        provider = db.embedding_provider
        cache = query_embeddings.get()
        if cache is None:
            embedding = await provider.get_embedding(query)
        else:
            key = (id(provider), query)
            if key not in cache:
                cache[key] = asyncio.create_task(provider.get_embedding(query))
            embedding = await asyncio.shield(cache[key])
        vector = np.asarray(embedding, dtype=np.float32).ravel()
        if vector.size != storage.dimension or not np.isfinite(vector).all():
            raise ValueError("Invalid query embedding")

    while True:
        if native:
            persister = get_async_persister(storage)
            if persister is not None:
                scores, indices = await persister.search(vector, fetch_k)
            else:
                scores, indices = await storage.search(vector, fetch_k)
            ids = [int(value) for value in indices[0] if value >= 0]
            documents = (
                await db.document_storage.get_documents(
                    metadata_filters=filters,
                    ids=ids,
                    limit=fetch_k,
                )
                if ids
                else []
            )
            by_id = {int(document["id"]): document for document in documents}
            raw = [
                SimpleNamespace(
                    data=by_id[int(doc_id)], similarity=1.0 - float(score) / 2.0
                )
                for score, doc_id in zip(scores[0], indices[0])
                if int(doc_id) in by_id
            ]
            exhausted = len(ids) < fetch_k
        else:
            # Older host adapters expose only retrieve; preserve their public API.
            raw = await db.retrieve(
                query=query,
                k=fetch_k,
                fetch_k=fetch_k,
                rerank=False,
                metadata_filters=filters or None,
            )
            exhausted = not filters and len(raw) < fetch_k

        results = []
        seen = set()
        for hit in raw:
            metadata = hit.data.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (ValueError, TypeError):
                    continue
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("status") or "active") != "active":
                continue
            if any(metadata.get(key) != value for key, value in filters.items()):
                continue
            identity = (
                hit.data.get("id") if source_key == "id" else metadata.get(source_key)
            )
            if identity is None or identity in seen:
                continue
            seen.add(identity)
            results.append(hit)
            if len(results) >= k:
                return results
        if exhausted or fetch_k >= cap:
            return results
        fetch_k = min(cap, fetch_k * 2)
