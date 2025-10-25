# -*- coding: utf-8 -*-
"""
server.py - LivingMemory WebUI backend
Provides authentication, memory browsing, detail view and bulk deletion APIs built on FastAPI.

WebUI 功能列表:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 记忆管理:
  - 查看记忆列表（分页、筛选、搜索）
  - 查看记忆详情（完整元数据和JSON）
  - 编辑记忆（内容、重要性、类型、状态）
  - 删除记忆（单个或批量删除）
  - 核爆清除（延迟确认机制）

⚙️ 系统管理:
  - 触发遗忘代理（手动清理低重要性记忆）
  - 重建稀疏索引（BM25索引重建）
  - 会话管理（查看活跃会话列表）

🛠️ 调试工具:
  - 检索测试（测试Dense/Sparse/Hybrid模式）
  - 融合策略对比（对比多种融合算法效果）
  - 记忆统计分析（分布统计和趋势分析）

📊 数据展示:
  - 实时统计（总记忆数、状态分布、活跃会话）
  - 分页浏览（支持自定义每页数量）
  - 关键词搜索（支持memory_id和内容搜索）
  - 状态筛选（活跃/已归档/已删除）

🔐 安全特性:
  - 密码认证（入口密码保护）
  - 会话管理（自动超时和清理）
  - 请求频率限制（防暴力破解）
  - Token有效期管理（绝对过期+活动超时）

API端点说明:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
认证相关:
  POST   /api/login                    - 用户登录
  POST   /api/logout                   - 用户登出
  GET    /api/health                   - 健康检查

记忆管理:
  GET    /api/memories                 - 获取记忆列表（支持分页、筛选、搜索）
  GET    /api/memories/{memory_id}     - 获取记忆详情
  PUT    /api/memories/{memory_id}     - 更新记忆字段
  DELETE /api/memories                 - 批量删除记忆
  POST   /api/memories/nuke            - 调度核爆任务
  GET    /api/memories/nuke            - 获取核爆状态
  DELETE /api/memories/nuke/{op_id}    - 取消核爆任务

系统管理:
  GET    /api/stats                           - 获取统计信息
  POST   /api/admin/forgetting-agent/trigger  - 手动触发遗忘代理
  POST   /api/admin/sparse-index/rebuild      - 重建稀疏索引
  GET    /api/admin/sessions                  - 获取会话列表

调试工具:
  POST   /api/debug/search-test          - 测试检索功能
  POST   /api/debug/fusion-comparison    - 对比融合策略
  GET    /api/debug/memory-analysis      - 分析记忆统计

注意事项:
  - 所有API（除login和health）都需要Bearer Token认证
  - Token有24小时绝对过期时间和可配置的活动超时时间
  - 核爆功能仅为视觉效果，不会真实删除数据（保护用户数据安全）
  - 编辑记忆会自动记录更新历史（时间、字段、原值、新值、原因）
"""


import asyncio
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple,List, TYPE_CHECKING

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from astrbot.api import logger

from ..core.utils import safe_parse_metadata
from ..storage.memory_storage import MemoryStorage

if TYPE_CHECKING:
    from ..storage.faiss_manager import FaissManager
    from ..main import SessionManager
    from ..core.engines.recall_engine import RecallEngine
    from ..core.engines.reflection_engine import ReflectionEngine
    from ..core.engines.forgetting_agent import ForgettingAgent
    from ..core.retrieval import SparseRetriever


class WebUIServer:
    """
    Helper class responsible for starting and managing the LivingMemory WebUI service.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        faiss_manager: "FaissManager",
        session_manager: Optional["SessionManager"] = None,
        recall_engine: Optional["RecallEngine"] = None,
        reflection_engine: Optional["ReflectionEngine"] = None,
        forgetting_agent: Optional["ForgettingAgent"] = None,
        sparse_retriever: Optional["SparseRetriever"] = None,
    ):
        self.config = config
        self.faiss_manager = faiss_manager
        self.session_manager = session_manager
        self.recall_engine = recall_engine
        self.reflection_engine = reflection_engine
        self.forgetting_agent = forgetting_agent
        self.sparse_retriever = sparse_retriever

        self.host = str(config.get("host", "127.0.0.1"))
        self.port = int(config.get("port", 8080))
        self.session_timeout = max(60, int(config.get("session_timeout", 3600)))
        self._access_password = str(config.get("access_password", "")).strip()

        # Token 管理 - 修复: 使用字典存储更多信息防止永不过期
        self._tokens: Dict[str, Dict[str, float]] = {}
        self._token_lock = asyncio.Lock()

        # 请求频率限制 - 新增: 防止暴力破解
        self._failed_attempts: Dict[str, List[float]] = {}
        self._attempt_lock = asyncio.Lock()

        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._nuke_task: Optional[asyncio.Task] = None

        self.memory_storage: Optional[MemoryStorage] = None
        self._storage_prepared = False
        self._pending_nuke: Optional[Dict[str, Any]] = None
        self._nuke_lock = asyncio.Lock()

        self._app = FastAPI(title="LivingMemory 控制台", version="1.3.3")
        self._setup_routes()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self):
        """
        启动 WebUI 服务。
        """
        if self._server_task and not self._server_task.done():
            logger.warning("WebUI 服务已经在运行")
            return

        await self._prepare_storage()

        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

        # 启动定期清理任务 - 新增: 防止内存泄漏
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        # 等待服务启动
        for _ in range(50):
            if getattr(self._server, "started", False):
                logger.info(f"WebUI 已启动: http://{self.host}:{self.port}")
                return
            if self._server_task.done():
                error = self._server_task.exception()
                raise RuntimeError(f"WebUI 启动失败: {error}") from error
            await asyncio.sleep(0.1)

        logger.warning("WebUI 启动耗时较长，仍在后台启动中")

    async def stop(self):
        """
        停止 WebUI 服务。
        """
        # 停止定期清理任务
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await self._server_task
        self._server = None
        self._server_task = None
        if self._nuke_task and not self._nuke_task.done():
            self._nuke_task.cancel()
            try:
                await self._nuke_task
            except asyncio.CancelledError:
                pass
        self._nuke_task = None
        self._pending_nuke = None
        self._cleanup_task = None
        logger.info("WebUI 已停止")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _periodic_cleanup(self):
        """
        定期清理过期 token 和失败尝试记录 - 新增: 防止内存泄漏
        """
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟清理一次
                async with self._token_lock:
                    await self._cleanup_tokens_locked()
                async with self._attempt_lock:
                    await self._cleanup_failed_attempts_locked()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定期清理任务出错: {e}")

    async def _cleanup_failed_attempts_locked(self):
        """
        清理过期的失败尝试记录 - 新增
        """
        now = time.time()
        expired_ips = []
        for ip, attempts in self._failed_attempts.items():
            # 只保留5分钟内的尝试记录
            recent = [t for t in attempts if now - t < 300]
            if recent:
                self._failed_attempts[ip] = recent
            else:
                expired_ips.append(ip)

        for ip in expired_ips:
            self._failed_attempts.pop(ip, None)

    async def _check_rate_limit(self, client_ip: str) -> bool:
        """
        检查请求频率限制 - 新增: 防止暴力破解

        Returns:
            bool: True 表示未超限, False 表示已超限
        """
        async with self._attempt_lock:
            await self._cleanup_failed_attempts_locked()
            attempts = self._failed_attempts.get(client_ip, [])
            recent = [t for t in attempts if time.time() - t < 300]  # 5分钟窗口

            if len(recent) >= 5:  # 5分钟内最多5次失败尝试
                return False
            return True

    async def _record_failed_attempt(self, client_ip: str):
        """
        记录失败的登录尝试 - 新增
        """
        async with self._attempt_lock:
            if client_ip not in self._failed_attempts:
                self._failed_attempts[client_ip] = []
            self._failed_attempts[client_ip].append(time.time())

    async def _prepare_storage(self):
        """
        初始化自定义记忆存储（如可用）。
        """
        if self._storage_prepared:
            return

        connection = None
        try:
            doc_storage = getattr(self.faiss_manager.db, "document_storage", None)
            connection = getattr(doc_storage, "connection", None)
        except Exception as exc:  # pragma: no cover
            logger.debug(f"获取文档存储连接失败: {exc}")

        if connection:
            try:
                storage = MemoryStorage(connection)
                await storage.initialize_schema()
                self.memory_storage = storage
                logger.info("WebUI 已接入插件自定义的记忆存储（SQLite）")
            except Exception as exc:
                logger.warning(f"初始化 MemoryStorage 失败，将回退至文档存储: {exc}")
                self.memory_storage = None
        else:
            logger.debug("未获取到 MemoryStorage 连接，将仅使用 Faiss 文档存储接口")

        self._storage_prepared = True

    def _setup_routes(self):
        """
        初始化 FastAPI 路由与静态资源。
        """
        static_dir = Path(__file__).resolve().parent.parent / "static"
        index_path = static_dir / "index.html"
        if not index_path.exists():
            logger.warning("未找到 WebUI 前端文件，静态资源目录为空")

        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                f"http://{self.host}:{self.port}",
                "http://localhost",
                "http://127.0.0.1",
            ],
            allow_methods=["GET", "POST", "PUT", "DELETE"],  # 添加 PUT 方法
            allow_headers=["Content-Type", "Authorization", "X-Auth-Token"],
            allow_credentials=True,
        )

        self._app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @self._app.get("/", response_class=HTMLResponse)
        async def serve_index():
            if not index_path.exists():
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="前端文件缺失")
            return HTMLResponse(index_path.read_text(encoding="utf-8"))

        @self._app.post("/api/login")
        async def login(request: Request, payload: Dict[str, Any]):
            password = str(payload.get("password", "")).strip()
            if not password:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="密码不能为空")

            # 检查请求频率限制 - 新增
            client_ip = request.client.host if request.client else "unknown"
            if not await self._check_rate_limit(client_ip):
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="尝试次数过多，请5分钟后再试"
                )

            if password != self._access_password:
                # 记录失败尝试 - 新增
                await self._record_failed_attempt(client_ip)
                await asyncio.sleep(1.0)  # 增加延迟到1秒，减缓暴力破解
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="认证失败")

            # 生成 token - 修复: 使用字典存储多个时间戳
            token = secrets.token_urlsafe(32)
            now = time.time()
            max_lifetime = 86400  # 24小时绝对过期

            async with self._token_lock:
                await self._cleanup_tokens_locked()
                self._tokens[token] = {
                    "created_at": now,
                    "last_active": now,
                    "max_lifetime": max_lifetime
                }

            return {"token": token, "expires_in": self.session_timeout}

        @self._app.post("/api/logout")
        async def logout(token: str = Depends(self._auth_dependency())):
            async with self._token_lock:
                self._tokens.pop(token, None)
            return {"detail": "已退出登录"}

        @self._app.get("/api/memories")
        async def list_memories(
            request: Request,
            token: str = Depends(self._auth_dependency()),
        ):
            query = request.query_params
            keyword = query.get("keyword", "").strip()
            status_filter = query.get("status", "all").strip() or "all"
            load_all = query.get("all", "false").lower() == "true"

            if load_all:
                page = 1
                page_size = 0
                offset = 0
            else:
                page = max(1, int(query.get("page", 1)))
                page_size = query.get("page_size")
                page_size = min(200, max(1, int(page_size))) if page_size else 50
                offset = (page - 1) * page_size

            try:
                total, items = await self._fetch_memories(
                    page=page,
                    page_size=page_size,
                    offset=offset,
                    status_filter=status_filter,
                    keyword=keyword,
                    load_all=load_all,
                )
            except Exception as exc:
                logger.error(f"获取记忆列表失败: {exc}", exc_info=True)
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, detail="读取记忆失败"
                ) from exc

            has_more = False if load_all else offset + len(items) < total
            effective_page_size = page_size if page_size else len(items)

            return {
                "items": items,
                "page": page,
                "page_size": effective_page_size,
                "total": total,
                "has_more": has_more,
            }

        @self._app.get("/api/memories/{memory_id}")
        async def memory_detail(
            memory_id: str, token: str = Depends(self._auth_dependency())
        ):
            detail = await self._get_memory_detail(memory_id)
            if not detail:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="未找到记忆记录")
            return detail

        @self._app.delete("/api/memories")
        async def delete_memories(
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            doc_ids = payload.get("doc_ids") or payload.get("ids") or []
            memory_ids = payload.get("memory_ids") or []

            if not doc_ids and not memory_ids:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="需要提供待删除的记忆ID列表"
                )

            deleted_docs = 0
            deleted_memories = 0

            if doc_ids:
                try:
                    doc_ids_int = [int(x) for x in doc_ids]
                    await self.faiss_manager.delete_memories(doc_ids_int)
                    deleted_docs = len(doc_ids_int)
                except Exception as exc:
                    logger.error(f"删除 Faiss 记忆失败: {exc}", exc_info=True)
                    raise HTTPException(
                        status.HTTP_500_INTERNAL_SERVER_ERROR, detail="向量记忆删除失败"
                    ) from exc

            if memory_ids and self.memory_storage:
                try:
                    ids = [str(x) for x in memory_ids]
                    await self.memory_storage.delete_memories_by_memory_ids(ids)
                    deleted_memories = len(ids)
                except Exception as exc:
                    logger.error(f"删除结构化记忆失败: {exc}", exc_info=True)
                    raise HTTPException(
                        status.HTTP_500_INTERNAL_SERVER_ERROR, detail="结构化记忆删除失败"
                    ) from exc

            return {
                "deleted_doc_count": deleted_docs,
                "deleted_memory_count": deleted_memories,
            }

        @self._app.post("/api/memories/nuke")
        async def schedule_memory_nuke(
            payload: Optional[Dict[str, Any]] = None,
            token: str = Depends(self._auth_dependency()),
        ):
            delay = 30
            if payload and "delay" in payload:
                try:
                    delay = int(payload["delay"])
                except (TypeError, ValueError):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, detail="delay 参数无效"
                    )
            return await self._schedule_nuke(delay)

        @self._app.get("/api/memories/nuke")
        async def get_memory_nuke_status(
            token: str = Depends(self._auth_dependency()),
        ):
            return await self._get_pending_nuke()

        @self._app.delete("/api/memories/nuke/{operation_id}")
        async def cancel_memory_nuke(
            operation_id: str,
            token: str = Depends(self._auth_dependency()),
        ):
            cancelled = await self._cancel_nuke(operation_id)
            if not cancelled:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="当前没有匹配的核爆任务"
                )
            return {"detail": "已取消核爆任务", "operation_id": operation_id}

        @self._app.get("/api/stats")
        async def stats(token: str = Depends(self._auth_dependency())):
            total, status_counts = await self._gather_statistics()
            active_sessions = (
                self.session_manager.get_session_count()
                if self.session_manager
                else 0
            )

            return {
                "total_memories": total,
                "status_breakdown": status_counts,
                "active_sessions": active_sessions,
                "session_timeout": self.session_timeout,
            }

        @self._app.get("/api/health")
        async def health():
            return {"status": "ok"}

        # ========== 记忆编辑 API ==========
        @self._app.put("/api/memories/{memory_id}")
        async def update_memory(
            memory_id: str,
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            """更新记忆字段"""
            return await self._update_memory_field(memory_id, payload)

        # ========== 系统管理 API ==========
        @self._app.post("/api/admin/forgetting-agent/trigger")
        async def trigger_forgetting_agent(
            token: str = Depends(self._auth_dependency()),
        ):
            """手动触发遗忘代理"""
            return await self._trigger_forgetting_agent()

        @self._app.post("/api/admin/sparse-index/rebuild")
        async def rebuild_sparse_index(
            token: str = Depends(self._auth_dependency()),
        ):
            """重建稀疏索引"""
            return await self._rebuild_sparse_index()

        @self._app.get("/api/admin/sessions")
        async def list_sessions(
            token: str = Depends(self._auth_dependency()),
        ):
            """获取会话列表"""
            return await self._get_sessions_info()

        # ========== 调试工具 API ==========
        @self._app.post("/api/debug/search-test")
        async def test_search(
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            """测试检索功能"""
            return await self._test_search(payload)

        @self._app.post("/api/debug/fusion-comparison")
        async def compare_fusion_strategies(
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            """对比融合策略"""
            return await self._compare_fusion_strategies(payload)

        @self._app.get("/api/debug/memory-analysis")
        async def analyze_memories(
            token: str = Depends(self._auth_dependency()),
        ):
            """分析记忆统计信息"""
            return await self._analyze_memories()

    async def _fetch_memories(
        self,
        page: int,
        page_size: int,
        offset: int,
        status_filter: str,
        keyword: str,
        load_all: bool,
    ) -> Tuple[int, list]:
        try:
            total, records = await self._query_faiss_memories(
                offset=offset,
                page_size=page_size,
                status_filter=status_filter,
                keyword=keyword,
                load_all=load_all,
            )
        except Exception as exc:
            logger.error(f"使用优化查询获取记忆失败，将回退基础实现: {exc}", exc_info=True)
            total, records = await self._fetch_memories_fallback(
                offset=offset,
                page_size=page_size,
                status_filter=status_filter,
                keyword=keyword,
                load_all=load_all,
            )

        items = [self._format_memory(record, source="faiss") for record in records]
        return total, items

    async def _query_faiss_memories(
        self,
        offset: int,
        page_size: int,
        status_filter: str,
        keyword: str,
        load_all: bool,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        doc_storage = getattr(self.faiss_manager.db, "document_storage", None)
        connection = getattr(doc_storage, "connection", None)
        if connection is None:
            raise RuntimeError("Document storage connection unavailable")

        conditions: List[str] = []
        params: List[Any] = []

        status_value = (status_filter or "").strip().lower()
        if status_value and status_value != "all":
            conditions.append("LOWER(COALESCE(json_extract(metadata, '$.status'), 'active')) = ?")
            params.append(status_value)

        keyword_value = (keyword or "").strip()
        if keyword_value:
            keyword_param = f"%{keyword_value.lower()}%"
            conditions.append("("
                "LOWER(text) LIKE ? OR "
                "LOWER(COALESCE(json_extract(metadata, '$.memory_content'), '')) LIKE ? OR "
                "LOWER(COALESCE(json_extract(metadata, '$.memory_id'), '')) LIKE ?"
                ")")
            params.extend([keyword_param, keyword_param, keyword_param])

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        count_sql = f"SELECT COUNT(*) FROM documents{where_clause}"
        async with connection.execute(count_sql, params) as cursor:
            row = await cursor.fetchone()
        total = int(row[0]) if row and row[0] is not None else 0

        query_sql = (
            "SELECT id, text, metadata FROM documents"
            f"{where_clause} ORDER BY id DESC"
        )
        query_params = list(params)
        if not load_all and page_size > 0:
            query_sql += " LIMIT ? OFFSET ?"
            query_params.extend([page_size, offset])

        async with connection.execute(query_sql, query_params) as cursor:
            rows = await cursor.fetchall()

        records: List[Dict[str, Any]] = []
        for row in rows:
            metadata_raw = row[2]
            if isinstance(metadata_raw, str):
                try:
                    metadata = json.loads(metadata_raw)
                except json.JSONDecodeError:
                    metadata = {}
            else:
                metadata = metadata_raw or {}

            records.append(
                {
                    "id": row[0],
                    "content": row[1],
                    "metadata": metadata,
                }
            )

        return total, records

    async def _fetch_memories_fallback(
        self,
        offset: int,
        page_size: int,
        status_filter: str,
        keyword: str,
        load_all: bool,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        total_available = await self.faiss_manager.count_total_memories()
        fetch_size = max(total_available, page_size if page_size else 0, 1)

        records = await self.faiss_manager.get_memories_paginated(
            page_size=fetch_size, offset=0
        )

        filtered_records = self._filter_records(records, status_filter, keyword)
        total_filtered = len(filtered_records)

        if load_all:
            return total_filtered, filtered_records

        start = max(0, offset)
        end = start + page_size if page_size else total_filtered
        return total_filtered, filtered_records[start:end]

    def _filter_records(
        self,
        records: List[Dict[str, Any]],
        status_filter: str,
        keyword: str
    ) -> List[Dict[str, Any]]:
        """
        在内存中过滤记录 - 新增: 支持状态和关键词筛选
        """
        filtered = []

        for record in records:
            # metadata 现在已经是字典
            metadata = record.get("metadata", {})

            # 状态过滤
            if status_filter and status_filter != "all":
                record_status = metadata.get("status", "active")
                if record_status != status_filter:
                    continue

            # 关键词过滤 (搜索 content 和 memory_content)
            if keyword:
                content = record.get("content", "")
                memory_content = metadata.get("memory_content", "")
                keyword_lower = keyword.lower()

                if (keyword_lower not in content.lower() and
                    keyword_lower not in memory_content.lower()):
                    continue

            filtered.append(record)

        return filtered

    async def _get_memory_detail(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个记忆详情 - 修复: 统一使用 Faiss 文档存储
        """
        # 尝试按文档ID查询
        try:
            doc_id = int(memory_id)
        except ValueError:
            # 如果不是整数,尝试按 memory_id 查询
            doc_id = None

        try:
            if doc_id is not None:
                # 按整数 ID 查询
                docs = await self.faiss_manager.db.document_storage.get_documents(
                    ids=[doc_id]
                )
            else:
                # 按 memory_id 查询 (在 metadata 中)
                all_docs = await self.faiss_manager.get_memories_paginated(
                    page_size=10000, offset=0
                )
                docs = [
                    doc for doc in all_docs
                    if doc.get("metadata", {}).get("memory_id") == memory_id
                ]

            if not docs:
                return None

            # metadata 已经是字典,直接返回
            return self._format_memory(docs[0], source="faiss")

        except Exception as exc:
            logger.error(f"查询记忆详情失败: {exc}", exc_info=True)
            return None

    def _format_memory(self, raw: Dict[str, Any], source: str) -> Dict[str, Any]:
        if source == "storage":
            memory_json = raw.get("memory_data") or "{}"
            parsed = self._safe_json_loads(memory_json)
            metadata = parsed.get("metadata", {})
            access_info = metadata.get("access_info", {})

            summary = (
                parsed.get("summary")
                or parsed.get("description")
                or parsed.get("memory_content")
                or ""
            )
            created_at = parsed.get("timestamp") or raw.get("timestamp")
            last_access = access_info.get("last_accessed_timestamp")

            return {
                "doc_id": None,
                "memory_id": raw.get("memory_id"),
                "summary": summary,
                "memory_type": raw.get("memory_type"),
                "importance": raw.get("importance_score"),
                "status": raw.get("status"),
                "created_at": self._format_timestamp(created_at),
                "last_access": self._format_timestamp(last_access),
                "source": "storage",
                "metadata": metadata,
                "raw": parsed,
                "raw_json": memory_json,
            }

        # Faiss source 的格式化 (修复: metadata 现在已经是字典)
        metadata = raw.get("metadata", {})  # ✅ 已经是字典,不需要 safe_parse_metadata

        # 优先使用 metadata.memory_content,fallback 到 content
        summary = metadata.get("memory_content") or raw.get("content") or ""
        importance = metadata.get("importance")
        event_type = metadata.get("event_type")
        status = metadata.get("status", "active")
        created_at = metadata.get("create_time")
        last_access = metadata.get("last_access_time")

        return {
            "doc_id": raw.get("id"),
            "memory_id": metadata.get("memory_id"),
            "summary": summary,
            "memory_type": event_type,
            "importance": importance,
            "status": status,
            "created_at": self._format_timestamp(created_at),
            "last_access": self._format_timestamp(last_access),
            "source": "faiss",
            "metadata": metadata,
            "raw": {
                "content": raw.get("content"),
                "metadata": metadata,
            },
            "raw_json": json.dumps(metadata, ensure_ascii=False),
        }

    async def _gather_statistics(self) -> Tuple[int, Dict[str, int]]:
        """
        统计记忆数量 - 修复: 统一使用 Faiss 文档存储
        """
        total = await self.faiss_manager.count_total_memories()
        counts = await self._collect_status_counts()
        return total, counts

    async def _collect_status_counts(self) -> Dict[str, int]:
        """
        针对 Faiss 文档存储统计不同状态的记忆数量。
        """
        counts: Dict[str, int] = {"active": 0, "archived": 0, "deleted": 0}
        try:
            conn = self.faiss_manager.db.document_storage.connection
            async with conn.execute(
                "SELECT json_extract(metadata, '$.status') AS status FROM documents"
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                status_value = row[0] if row and row[0] else "active"
                counts[status_value] = counts.get(status_value, 0) + 1
        except Exception as exc:
            logger.error(f"统计记忆状态失败: {exc}", exc_info=True)
        return counts

    def _serialize_nuke_status(
        self,
        payload: Optional[Dict[str, Any]],
        now: Optional[float] = None,
        already_pending: bool = False,
    ) -> Dict[str, Any]:
        if not payload:
            return {"pending": False}

        now = now or time.time()
        execute_at = float(payload.get("execute_at", now))
        seconds_left = max(0, int(round(execute_at - now)))
        if already_pending:
            detail = "A pending wipe is already counting down"
        else:
            detail = (
                f"Wipe executes in {seconds_left} seconds"
                if seconds_left
                else "Wipe executing now"
            )

        return {
            "pending": True,
            "operation_id": payload.get("id"),
            "execute_at": datetime.fromtimestamp(execute_at).isoformat(
                sep=" ", timespec="seconds"
            ),
            "seconds_left": seconds_left,
            "detail": detail,
            "already_pending": already_pending,
        }

    async def _schedule_nuke(self, delay_seconds: int) -> Dict[str, Any]:
        delay = max(5, min(int(delay_seconds), 600))
        task_to_cancel: Optional[asyncio.Task] = None
        pending_snapshot: Dict[str, Any]

        async with self._nuke_lock:
            now = time.time()
            if self._pending_nuke and self._pending_nuke.get("status") == "scheduled":
                return self._serialize_nuke_status(self._pending_nuke, now, True)

            if self._nuke_task and not self._nuke_task.done():
                task_to_cancel = self._nuke_task

            operation_id = secrets.token_urlsafe(8)
            execute_at = now + delay
            pending = {
                "id": operation_id,
                "created_at": now,
                "execute_at": execute_at,
                "status": "scheduled",
            }
            self._pending_nuke = pending
            self._nuke_task = asyncio.create_task(self._run_nuke(operation_id, delay))
            pending_snapshot = dict(pending)

        if task_to_cancel:
            task_to_cancel.cancel()
            try:
                await task_to_cancel
            except asyncio.CancelledError:
                pass

        return self._serialize_nuke_status(pending_snapshot, time.time())

    async def _get_pending_nuke(self) -> Dict[str, Any]:
        async with self._nuke_lock:
            pending = self._pending_nuke
            if not pending or pending.get("status") != "scheduled":
                return {"pending": False}
            snapshot = dict(pending)
        return self._serialize_nuke_status(snapshot)

    async def _cancel_nuke(self, operation_id: str) -> bool:
        task: Optional[asyncio.Task] = None
        async with self._nuke_lock:
            if not self._pending_nuke or self._pending_nuke.get("id") != operation_id:
                return False
            task = self._nuke_task
            self._pending_nuke = None
            self._nuke_task = None

        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return True

    async def _run_nuke(self, operation_id: str, delay: int):
        try:
            await asyncio.sleep(delay)
            await self._execute_nuke(operation_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            logger.error("Nuke job failed: %s", exc, exc_info=True)
            async with self._nuke_lock:
                if self._pending_nuke and self._pending_nuke.get("id") == operation_id:
                    self._pending_nuke = None
                self._nuke_task = None

    async def _execute_nuke(self, operation_id: str):
        async with self._nuke_lock:
            if not self._pending_nuke or self._pending_nuke.get("id") != operation_id:
                return
            self._pending_nuke["status"] = "running"

        vector_deleted = 0
        storage_deleted = 0

        # 🎭 核爆功能仅为视觉效果，不会真实删除数据
        # 这是一个娱乐性的视觉特效，保护用户数据安全
        try:
            # 只统计数量用于显示，不执行任何删除操作
            logger.info("核爆视觉效果触发：这只是模拟，不会删除任何数据")

            # 统计记忆数量用于显示
            async with self.faiss_manager.db.document_storage.connection.execute(
                "SELECT COUNT(*) FROM documents"
            ) as cursor:
                row = await cursor.fetchone()
                vector_deleted = row[0] if row else 0

            if self.memory_storage:
                async with self.memory_storage.connection.execute(
                    "SELECT COUNT(*) FROM memories"
                ) as cursor:
                    row = await cursor.fetchone()
                    storage_deleted = row[0] if row else 0

            logger.info(
                "核爆视觉效果完成：模拟清除 %s 条向量记录和 %s 条结构化记录（实际数据完全未受影响）",
                vector_deleted,
                storage_deleted,
            )
        except Exception as exc:
            logger.error("Memory wipe failed: %s", exc, exc_info=True)
        finally:
            async with self._nuke_lock:
                if self._pending_nuke and self._pending_nuke.get("id") == operation_id:
                    self._pending_nuke = None
                self._nuke_task = None

    def _auth_dependency(self):
        async def dependency(request: Request) -> str:
            token = self._extract_token(request)
            if not token:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="未授权")
            await self._validate_token(token)
            return token

        return dependency

    async def _validate_token(self, token: str):
        """
        验证 token - 修复: 检查绝对过期时间和会话超时
        """
        async with self._token_lock:
            await self._cleanup_tokens_locked()
            token_data = self._tokens.get(token)

            if not token_data:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="会话已失效")

            now = time.time()

            # 检查绝对过期时间 (24小时)
            if now - token_data["created_at"] > token_data["max_lifetime"]:
                self._tokens.pop(token, None)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="会话已达最大时长")

            # 检查会话超时 (最后活动时间)
            if now - token_data["last_active"] > self.session_timeout:
                self._tokens.pop(token, None)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="会话已过期")

            # 更新最后活动时间
            token_data["last_active"] = now

    async def _cleanup_tokens_locked(self):
        """
        清理过期 token - 修复: 适配新的 token 数据结构
        """
        now = time.time()
        expired = []

        for token, token_data in self._tokens.items():
            # 检查是否超过绝对过期时间或会话超时
            if (now - token_data["created_at"] > token_data["max_lifetime"] or
                now - token_data["last_active"] > self.session_timeout):
                expired.append(token)

        for token in expired:
            self._tokens.pop(token, None)

    def _extract_token(self, request: Request) -> str:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        cookie_token = request.cookies.get("auth_token")
        if cookie_token:
            return cookie_token.strip()
        custom_header = request.headers.get("X-Auth-Token", "")
        return custom_header.strip()

    @staticmethod
    def _safe_json_loads(payload: str) -> Dict[str, Any]:
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _format_timestamp(value: Any) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value).isoformat(sep=" ", timespec="seconds")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(
                    sep=" ", timespec="seconds"
                )
            except ValueError:
                return value
        return str(value)

    # ========== 记忆编辑 API 实现 ==========
    async def _update_memory_field(self, memory_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """更新记忆字段"""
        field = payload.get("field")
        value = payload.get("value")
        reason = payload.get("reason", "WebUI 手动更新")

        if not field or value is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="缺少必要参数: field 和 value")

        # 允许编辑的字段
        allowed_fields = ["content", "importance", "type", "status"]
        if field not in allowed_fields:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"不支持编辑字段: {field}")

        try:
            # 获取记忆详情
            detail = await self._get_memory_detail(memory_id)
            if not detail:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="未找到记忆记录")

            doc_id = detail.get("doc_id")
            if doc_id is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="无法获取记忆的文档ID")

            # 获取完整文档
            docs = await self.faiss_manager.db.document_storage.get_documents(ids=[doc_id])
            if not docs:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="未找到文档")

            doc = docs[0]
            metadata = doc.get("metadata", {})

            # 更新字段
            if field == "content":
                metadata["memory_content"] = value
            elif field == "importance":
                try:
                    metadata["importance"] = float(value)
                except (ValueError, TypeError):
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="重要性必须是数字")
            elif field == "type":
                metadata["event_type"] = value
            elif field == "status":
                if value not in ["active", "archived", "deleted"]:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="状态值无效")
                metadata["status"] = value

            # 添加更新历史
            if "update_history" not in metadata:
                metadata["update_history"] = []

            metadata["update_history"].append({
                "timestamp": time.time(),
                "field": field,
                "old_value": detail["metadata"].get(field),
                "new_value": value,
                "reason": reason
            })

            # 更新文档
            await self.faiss_manager.db.document_storage.update_document(
                doc_id=doc_id,
                text=doc.get("text", ""),
                metadata=metadata
            )

            return {
                "success": True,
                "message": f"成功更新字段: {field}",
                "updated_field": field,
                "new_value": value
            }

        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"更新记忆字段失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="更新失败") from exc

    # ========== 系统管理 API 实现 ==========
    async def _trigger_forgetting_agent(self) -> Dict[str, Any]:
        """手动触发遗忘代理"""
        if not self.forgetting_agent:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="遗忘代理未初始化")

        try:
            # 触发遗忘任务，直接返回原始结果
            result = await self.forgetting_agent.trigger_manual_run()
            return result
        except Exception as exc:
            logger.error(f"触发遗忘代理失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="遗忘代理执行失败") from exc

    async def _rebuild_sparse_index(self) -> Dict[str, Any]:
        """重建稀疏索引"""
        if not self.sparse_retriever:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="稀疏检索器未启用")

        try:
            # 获取所有记忆数量
            total = await self.faiss_manager.count_total_memories()
            
            # 直接调用 rebuild_index，它会自动清空并重建索引
            await self.sparse_retriever.rebuild_index()

            return {
                "success": True,
                "message": "稀疏索引重建完成",
                "indexed_count": total
            }
        except Exception as exc:
            logger.error(f"重建稀疏索引失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="重建索引失败") from exc

    async def _get_sessions_info(self) -> Dict[str, Any]:
        """获取会话信息"""
        if not self.session_manager:
            return {
                "total_sessions": 0,
                "sessions": [],
                "max_sessions": 0,
                "session_ttl": 0
            }

        try:
            sessions = []
            for session_id, session_data in self.session_manager._sessions.items():
                last_access = self.session_manager._access_times.get(session_id, 0)
                sessions.append({
                    "session_id": session_id,
                    "round_count": session_data.get("round_count", 0),
                    "history_size": len(session_data.get("history", [])),
                    "last_access": self._format_timestamp(last_access)
                })

            # 按最后访问时间排序
            sessions.sort(key=lambda x: x.get("last_access", ""), reverse=True)

            return {
                "total_sessions": len(sessions),
                "sessions": sessions,
                "max_sessions": self.session_manager.max_sessions,
                "session_ttl": self.session_manager.session_ttl
            }
        except Exception as exc:
            logger.error(f"获取会话信息失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取会话信息失败") from exc

    # ========== 调试工具 API 实现 ==========
    async def _test_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """测试检索功能"""
        if not self.recall_engine:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="检索引擎未初始化")

        query = payload.get("query", "").strip()
        if not query:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="查询不能为空")

        mode = payload.get("mode", "hybrid")
        top_k = payload.get("top_k", 5)

        if mode not in ["dense", "sparse", "hybrid"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="无效的检索模式")

        try:
            # 保存原始配置
            original_mode = self.recall_engine.config.get("mode")
            original_top_k = self.recall_engine.config.get("top_k")

            # 临时更改配置
            self.recall_engine.config["mode"] = mode
            self.recall_engine.config["top_k"] = top_k

            start_time = time.time()
            # 直接使用 faiss_manager 的 context，如果没有则传 None
            context = getattr(self.faiss_manager, 'context', None)
            results = await self.recall_engine.recall(context, query)
            elapsed_time = time.time() - start_time

            # 恢复配置
            self.recall_engine.config["mode"] = original_mode
            self.recall_engine.config["top_k"] = original_top_k

            # 格式化结果
            formatted_results = []
            for mem in results:
                formatted_results.append({
                    "content": mem.get("metadata", {}).get("memory_content") or mem.get("content", ""),
                    "importance": mem.get("metadata", {}).get("importance"),
                    "score": mem.get("score", 0),
                    "memory_type": mem.get("metadata", {}).get("event_type"),
                    "created_at": self._format_timestamp(mem.get("metadata", {}).get("create_time"))
                })

            return {
                "query": query,
                "mode": mode,
                "top_k": top_k,
                "elapsed_time": round(elapsed_time, 3),
                "result_count": len(formatted_results),
                "results": formatted_results
            }

        except Exception as exc:
            logger.error(f"测试检索失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="检索测试失败") from exc

    async def _compare_fusion_strategies(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """对比融合策略"""
        if not self.recall_engine:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="检索引擎未初始化")

        query = payload.get("query", "").strip()
        if not query:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="查询不能为空")

        strategies = payload.get("strategies", ["rrf", "hybrid_rrf", "weighted"])
        top_k = payload.get("top_k", 5)

        try:
            # 保存原始配置
            original_strategy = self.recall_engine.config.get("fusion", {}).get("strategy")
            original_top_k = self.recall_engine.config.get("top_k")

            comparison_results = []
            context = getattr(self.faiss_manager, 'context', None)

            for strategy in strategies:
                # 设置策略
                self.recall_engine.config.setdefault("fusion", {})["strategy"] = strategy
                self.recall_engine.config["top_k"] = top_k
                self.recall_engine.config["mode"] = "hybrid"

                start_time = time.time()
                results = await self.recall_engine.recall(context, query)
                elapsed_time = time.time() - start_time

                comparison_results.append({
                    "strategy": strategy,
                    "elapsed_time": round(elapsed_time, 3),
                    "result_count": len(results),
                    "top_3_scores": [r.get("score", 0) for r in results[:3]]
                })

            # 恢复配置
            if original_strategy:
                self.recall_engine.config["fusion"]["strategy"] = original_strategy
            self.recall_engine.config["top_k"] = original_top_k

            return {
                "query": query,
                "strategies_tested": len(strategies),
                "comparison": comparison_results
            }

        except Exception as exc:
            logger.error(f"对比融合策略失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="策略对比失败") from exc

    async def _analyze_memories(self) -> Dict[str, Any]:
        """分析记忆统计信息"""
        try:
            # 获取所有记忆
            total = await self.faiss_manager.count_total_memories()
            memories = await self.faiss_manager.get_memories_paginated(page_size=total, offset=0)

            # 统计分析
            importance_distribution = {"0-3": 0, "3-5": 0, "5-7": 0, "7-10": 0}
            type_distribution = {}
            status_distribution = {"active": 0, "archived": 0, "deleted": 0}

            total_importance = 0
            count_with_importance = 0

            for mem in memories:
                metadata = mem.get("metadata", {})

                # 重要性分布
                importance = metadata.get("importance", 0)
                if importance is not None:
                    total_importance += importance
                    count_with_importance += 1

                    if importance < 3:
                        importance_distribution["0-3"] += 1
                    elif importance < 5:
                        importance_distribution["3-5"] += 1
                    elif importance < 7:
                        importance_distribution["5-7"] += 1
                    else:
                        importance_distribution["7-10"] += 1

                # 类型分布
                mem_type = metadata.get("event_type", "unknown")
                type_distribution[mem_type] = type_distribution.get(mem_type, 0) + 1

                # 状态分布
                status = metadata.get("status", "active")
                status_distribution[status] = status_distribution.get(status, 0) + 1

            avg_importance = total_importance / count_with_importance if count_with_importance > 0 else 0

            return {
                "total_memories": total,
                "average_importance": round(avg_importance, 2),
                "importance_distribution": importance_distribution,
                "type_distribution": type_distribution,
                "status_distribution": status_distribution
            }

        except Exception as exc:
            logger.error(f"分析记忆失败: {exc}", exc_info=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="记忆分析失败") from exc
