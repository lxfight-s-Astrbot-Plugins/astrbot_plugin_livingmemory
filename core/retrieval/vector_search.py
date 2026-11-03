"""Shared query embeddings and bounded, scope-aware vector candidate expansion."""

import asyncio
import json
from contextvars import ContextVar
from types import SimpleNamespace

from ..faiss_async_persist import get_async_persister
from ..memory_scope import adapter_sql, adapter_visible, current_adapter_access

query_embeddings = ContextVar("livingmemory_query_embeddings", default=None)


async def retrieve_documents(
    db, metadata_filters, ids=None, limit=100, offset=0, *, ids_only=False
):
    """Apply request ownership in SQL before limiting host document reads."""
    predicate, adapters = adapter_sql("metadata")
    if not adapters:
        return await db.document_storage.get_documents(
            metadata_filters=metadata_filters, ids=ids, limit=limit, offset=offset
        )
    from sqlalchemy import text

    values = {f"adapter_{i}": value for i, value in enumerate(adapters)}
    for i in range(len(adapters)):
        predicate = predicate.replace("?", f":adapter_{i}", 1)
    conditions = [predicate]
    for key, value in metadata_filters.items():
        conditions.append(f"json_extract(metadata, '$.{key}') = :filter_{key}")
        values[f"filter_{key}"] = value
    if ids is not None:
        if not ids:
            return []
        conditions.append(
            "id IN (" + ",".join(f":id_{i}" for i in range(len(ids))) + ")"
        )
        values.update({f"id_{i}": value for i, value in enumerate(ids)})
    columns = "id" if ids_only else "id, doc_id, text, metadata"
    sql = f"SELECT {columns} FROM documents WHERE " + " AND ".join(conditions)
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        values.update(limit=limit, offset=offset or 0)
    async with db.document_storage.get_session() as session:
        rows = (await session.execute(text(sql), values)).mappings().all()
    if ids_only:
        return [dict(row) for row in rows]
    return [dict(row, metadata=json.loads(row["metadata"] or "{}")) for row in rows]


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

    search_options = {}
    if native:
        import numpy as np

        access = current_adapter_access.get()
        if access is not None and access.adapter_ids is not None:
            import faiss

            candidates = await retrieve_documents(
                db, filters, limit=None, ids_only=True
            )
            if not candidates:
                return []
            allowed_ids = np.asarray([row["id"] for row in candidates], dtype=np.int64)
            search_options["params"] = faiss.SearchParameters(
                sel=faiss.IDSelectorBatch(allowed_ids)
            )
            cap = min(len(candidates), cap)
            fetch_k = min(fetch_k, cap)

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

    # 候选窗口翻倍重查时复用已取回的文档，避免每轮重复 SQL + JSON 解析；
    # 每轮只需查询新增的 kNN id。
    documents_by_id: dict[int, dict] = {}

    while True:
        if native:
            persister = get_async_persister(storage)
            if persister is not None:
                scores, indices = await persister.search(
                    vector, fetch_k, **search_options
                )
            elif search_options:
                scores, indices = storage.index.search(
                    vector.reshape(1, -1), fetch_k, **search_options
                )
            else:
                scores, indices = await storage.search(vector, fetch_k)
            ids = [int(value) for value in indices[0] if value >= 0]
            new_ids = [doc_id for doc_id in ids if doc_id not in documents_by_id]
            if new_ids:
                documents = await retrieve_documents(
                    db,
                    metadata_filters=filters,
                    ids=new_ids,
                    limit=len(new_ids),
                )
                documents_by_id.update(
                    {int(document["id"]): document for document in documents}
                )
            by_id = documents_by_id
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
            if not adapter_visible(metadata):
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
