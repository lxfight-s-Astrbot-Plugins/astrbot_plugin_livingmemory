# -*- coding: utf-8 -*-
"""
server.py - LivingMemory WebUI backend (适配MemoryEngine架构)
基于FastAPI提供记忆管理、统计分析和系统管理API

WebUI 功能列表:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 记忆管理:
  - 查看记忆列表（分页、筛选、搜索）
  - 查看记忆详情
  - 搜索记忆
  - 删除记忆（单个或批量）

️ 系统管理:
  - 清理旧记忆
  - 查看会话列表
  - 获取配置信息

 数据展示:
  - 实时统计（总记忆数、会话分布）
  - 分页浏览
  - 关键词搜索

 安全特性:
  - 密码认证
  - Token管理
  - 请求频率限制

API端点说明:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
认证相关:
  POST   /api/login                    - 用户登录
  POST   /api/logout                   - 用户登出
  GET    /api/health                   - 健康检查

记忆管理:
  GET    /api/memories                 - 获取记忆列表
  GET    /api/memories/{memory_id}     - 获取记忆详情
  POST   /api/memories/search          - 搜索记忆
  DELETE /api/memories/{memory_id}     - 删除单个记忆
  POST   /api/memories/batch-delete    - 批量删除记忆

系统管理:
  GET    /api/stats                    - 获取统计信息
  POST   /api/cleanup                  - 清理旧记忆
  GET    /api/sessions                 - 获取会话列表
  GET    /api/config                   - 获取配置信息
"""

import asyncio
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, List

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from astrbot.api import logger


class WebUIServer:
    """
    WebUI服务器 - 基于MemoryEngine和ConversationManager架构
    """

    def __init__(
        self,
        memory_engine,
        config: Dict[str, Any],
        conversation_manager=None,
        index_validator=None,
    ):
        """
        初始化WebUI服务器

        Args:
            memory_engine: MemoryEngine实例
            config: 配置字典,包含:
                - host: 监听地址
                - port: 监听端口
                - access_password: 访问密码
                - session_timeout: 会话超时时间
            conversation_manager: ConversationManager实例(可选)
            index_validator: IndexValidator实例(可选)
        """
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self.config = config

        self.host = str(config.get("host", "127.0.0.1"))
        self.port = int(config.get("port", 8080))
        self.session_timeout = max(60, int(config.get("session_timeout", 3600)))
        self._access_password = str(config.get("access_password", "")).strip()
        self._password_generated = False
        if not self._access_password:
            self._access_password = secrets.token_urlsafe(10)
            self._password_generated = True
            logger.info(
                "WebUI 未设置访问密码，已自动生成随机密码: %s",
                self._access_password,
            )

        # Token管理
        self._tokens: Dict[str, Dict[str, float]] = {}
        self._token_lock = asyncio.Lock()

        # 请求频率限制
        self._failed_attempts: Dict[str, List[float]] = {}
        self._attempt_lock = asyncio.Lock()

        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

        self._app = FastAPI(title="LivingMemory WebUI", version="2.0.0")
        self._setup_routes()

    # ------------------------------------------------------------------
    # 公共API
    # ------------------------------------------------------------------

    async def start(self):
        """启动WebUI服务"""
        if self._server_task and not self._server_task.done():
            logger.warning("WebUI 服务已经在运行")
            return

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

        # 启动定期清理任务
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
        """停止WebUI服务"""
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
        self._cleanup_task = None
        logger.info("WebUI 已停止")

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    async def _periodic_cleanup(self):
        """定期清理过期token和失败尝试记录"""
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

    async def _cleanup_tokens_locked(self):
        """清理过期的token"""
        now = time.time()
        expired_tokens = []
        for token, token_info in self._tokens.items():
            created_at = token_info.get("created_at", 0)
            last_active = token_info.get("last_active", 0)
            max_lifetime = token_info.get("max_lifetime", 86400)

            # 检查绝对过期时间
            if now - created_at > max_lifetime:
                expired_tokens.append(token)
            # 检查活动超时
            elif now - last_active > self.session_timeout:
                expired_tokens.append(token)

        for token in expired_tokens:
            self._tokens.pop(token, None)

    async def _cleanup_failed_attempts_locked(self):
        """清理过期的失败尝试记录"""
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
        检查请求频率限制

        Returns:
            bool: True表示未超限, False表示已超限
        """
        async with self._attempt_lock:
            await self._cleanup_failed_attempts_locked()
            attempts = self._failed_attempts.get(client_ip, [])
            recent = [t for t in attempts if time.time() - t < 300]

            if len(recent) >= 5:  # 5分钟内最多5次失败尝试
                return False
            return True

    async def _record_failed_attempt(self, client_ip: str):
        """记录失败的登录尝试"""
        async with self._attempt_lock:
            if client_ip not in self._failed_attempts:
                self._failed_attempts[client_ip] = []
            self._failed_attempts[client_ip].append(time.time())

    def _auth_dependency(self):
        """认证依赖"""

        async def dependency(request: Request) -> str:
            token = self._extract_token(request)
            await self._validate_token(token)
            return token

        return dependency

    async def _validate_token(self, token: str):
        """验证token有效性"""
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证Token"
            )

        async with self._token_lock:
            token_info = self._tokens.get(token)
            if not token_info:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token无效或已过期"
                )

            now = time.time()
            created_at = token_info.get("created_at", 0)
            last_active = token_info.get("last_active", 0)
            max_lifetime = token_info.get("max_lifetime", 86400)

            # 检查绝对过期时间
            if now - created_at > max_lifetime:
                self._tokens.pop(token, None)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token已过期"
                )

            # 检查活动超时
            if now - last_active > self.session_timeout:
                self._tokens.pop(token, None)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已超时"
                )

            # 更新最后活动时间
            token_info["last_active"] = now

    def _extract_token(self, request: Request) -> str:
        """从请求中提取token"""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]

        # 也支持X-Auth-Token header
        return request.headers.get("X-Auth-Token", "")

    def _setup_routes(self):
        """初始化FastAPI路由与静态资源"""
        static_dir = Path(__file__).resolve().parent.parent / "static"
        index_path = static_dir / "index.html"

        if not index_path.exists():
            logger.warning("未找到 WebUI 前端文件，静态资源目录为空")

        # CORS配置
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                f"http://{self.host}:{self.port}",
                "http://localhost",
                "http://127.0.0.1",
            ],
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "Authorization", "X-Auth-Token"],
            allow_credentials=True,
        )

        # 静态文件
        if static_dir.exists():
            self._app.mount("/static", StaticFiles(directory=static_dir), name="static")

        # 首页
        @self._app.get("/", response_class=HTMLResponse)
        async def serve_index():
            if not index_path.exists():
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="前端文件缺失")
            return HTMLResponse(index_path.read_text(encoding="utf-8"))

        # 健康检查
        @self._app.get("/api/health")
        async def health():
            return {"status": "ok", "version": "2.0.0"}

        # 登录
        @self._app.post("/api/login")
        async def login(request: Request, payload: Dict[str, Any]):
            password = str(payload.get("password", "")).strip()
            if not password:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="密码不能为空")

            # 检查请求频率限制
            client_ip = request.client.host if request.client else "unknown"
            if not await self._check_rate_limit(client_ip):
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="尝试次数过多，请5分钟后再试",
                )

            if password != self._access_password:
                # 记录失败尝试
                await self._record_failed_attempt(client_ip)
                await asyncio.sleep(1.0)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="认证失败")

            # 生成token
            token = secrets.token_urlsafe(32)
            now = time.time()
            max_lifetime = 86400  # 24小时绝对过期

            async with self._token_lock:
                await self._cleanup_tokens_locked()
                self._tokens[token] = {
                    "created_at": now,
                    "last_active": now,
                    "max_lifetime": max_lifetime,
                }

            return {"token": token, "expires_in": self.session_timeout}

        # 登出
        @self._app.post("/api/logout")
        async def logout(token: str = Depends(self._auth_dependency())):
            async with self._token_lock:
                self._tokens.pop(token, None)
            return {"detail": "已退出登录"}

        # 获取记忆列表（支持服务端分页）
        @self._app.get("/api/memories")
        async def list_memories(
            request: Request,
            token: str = Depends(self._auth_dependency()),
        ):
            query = request.query_params
            session_id = query.get("session_id")
            page = int(query.get("page", 1))
            page_size = int(query.get("page_size", 20))

            # 限制每页最大数量，防止内存溢出
            page_size = min(page_size, 100)
            offset = (page - 1) * page_size

            try:
                if session_id:
                    # 获取特定会话的记忆
                    memories = await self.memory_engine.get_session_memories(
                        session_id=session_id, limit=page_size
                    )
                    total = len(memories)
                else:
                    # 先获取总数（高效，不加载数据）
                    total = await self.memory_engine.faiss_db.document_storage.count_documents(
                        metadata_filters={}
                    )

                    # 使用真正的服务端分页（只加载当前页数据）
                    all_docs = await self.memory_engine.faiss_db.document_storage.get_documents(
                        metadata_filters={}, limit=page_size, offset=offset
                    )

                    # 解析 metadata 字段（从 JSON 字符串转为字典）
                    import json

                    for doc in all_docs:
                        if isinstance(doc.get("metadata"), str):
                            try:
                                doc["metadata"] = json.loads(doc["metadata"])
                            except (json.JSONDecodeError, TypeError):
                                doc["metadata"] = {}

                    memories = all_docs

                return {
                    "success": True,
                    "data": {
                        "items": memories,
                        "total": total,
                        "page": page,
                        "page_size": page_size,
                        "has_more": (offset + page_size) < total,
                    },
                }
            except Exception as e:
                logger.error(f"获取记忆列表失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取记忆详情
        @self._app.get("/api/memories/{memory_id}")
        async def get_memory_detail(
            memory_id: int, token: str = Depends(self._auth_dependency())
        ):
            try:
                memory = await self.memory_engine.get_memory(memory_id)
                if not memory:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记忆不存在")

                return {"success": True, "data": memory}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"获取记忆详情失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 搜索记忆
        @self._app.post("/api/memories/search")
        async def search_memories(
            payload: Dict[str, Any], token: str = Depends(self._auth_dependency())
        ):
            query = payload.get("query", "").strip()
            if not query:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="查询内容不能为空"
                )

            k = min(50, max(1, int(payload.get("k", 10))))
            session_id = payload.get("session_id")
            persona_id = payload.get("persona_id")
            try:
                results = await self.memory_engine.search_memories(
                    query=query, k=k, session_id=session_id, persona_id=persona_id
                )

                # 格式化结果
                formatted_results = []
                for result in results:
                    formatted_results.append(
                        {
                            "id": result.doc_id,
                            "content": result.content,
                            "score": result.final_score,
                            "metadata": result.metadata,
                        }
                    )

                return {"success": True, "data": formatted_results}
            except Exception as e:
                logger.error(f"搜索记忆失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 删除单个记忆
        @self._app.delete("/api/memories/{memory_id}")
        async def delete_memory(
            memory_id: int, token: str = Depends(self._auth_dependency())
        ):
            try:
                success = await self.memory_engine.delete_memory(memory_id)
                if not success:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记忆不存在")

                return {"success": True, "message": f"记忆 {memory_id} 已删除"}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"删除记忆失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 编辑记忆
        @self._app.put("/api/memories/{memory_id}")
        async def update_memory(
            memory_id: int,
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            """
            编辑指定记忆
            支持编辑字段: content, importance, type, status
            """
            try:
                field = payload.get("field")
                value = payload.get("value")
                reason = payload.get("reason", "")

                if not field or value is None:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, detail="需要指定 field 和 value"
                    )

                # 获取记忆详情
                memory = await self.memory_engine.get_memory(memory_id)
                if not memory:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记忆不存在")

                # 验证字段和值
                valid_fields = {"content", "importance", "type", "status"}
                if field not in valid_fields:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, detail=f"不支持编辑字段: {field}"
                    )

                # 构建更新字典
                updates = {}

                # 类型转换和验证
                if field == "importance":
                    try:
                        value = float(value)
                        if not (0 <= value <= 10):
                            raise ValueError
                        # 转换为 0-1 范围（MemoryEngine 使用此范围）
                        value = value / 10.0
                    except (ValueError, TypeError):
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            detail="重要性必须是 0-10 之间的数字",
                        )
                    updates["importance"] = value

                elif field == "status":
                    valid_statuses = {"active", "archived", "deleted"}
                    if value not in valid_statuses:
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            detail=f"无效的状态。允许值: {', '.join(valid_statuses)}",
                        )
                    # 状态存储在 metadata 中
                    updates["metadata"] = {"status": value}

                elif field == "type":
                    # 类型也存储在 metadata 中
                    updates["metadata"] = {"memory_type": str(value).strip()}

                elif field == "content":
                    # 内容直接更新
                    updates["content"] = str(value).strip()

                # 添加更新原因到元数据
                if reason:
                    if "metadata" not in updates:
                        updates["metadata"] = {}
                    updates["metadata"]["update_reason"] = reason

                # 调用 MemoryEngine 的更新方法
                success = await self.memory_engine.update_memory(memory_id, updates)
                if not success:
                    raise HTTPException(
                        status.HTTP_500_INTERNAL_SERVER_ERROR, detail="更新失败"
                    )

                return {
                    "success": True,
                    "message": f"记忆 {memory_id} 的 {field} 已更新",
                    "data": {"memory_id": memory_id, "field": field, "value": value},
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"更新记忆失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 批量删除记忆
        @self._app.post("/api/memories/batch-delete")
        async def batch_delete_memories(
            payload: Dict[str, Any], token: str = Depends(self._auth_dependency())
        ):
            memory_ids = payload.get("memory_ids", [])
            if not memory_ids:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="需要提供记忆ID列表"
                )

            try:
                deleted_count = 0
                failed_count = 0
                failed_ids = []  # 记录失败的 ID 用于诊断

                logger.info(
                    f"[批量删除] 准备删除 {len(memory_ids)} 条记忆: {memory_ids}"
                )

                for memory_id in memory_ids:
                    try:
                        # 转换为整数
                        mid = int(memory_id)
                        logger.debug(f"[批量删除] 尝试删除 memory_id={mid}")

                        success = await self.memory_engine.delete_memory(mid)
                        if success:
                            deleted_count += 1
                            logger.debug(f"[批量删除] 成功删除 memory_id={mid}")
                        else:
                            failed_count += 1
                            failed_ids.append(mid)
                            logger.warning(
                                f"[批量删除]  删除失败 memory_id={mid} (引擎返回False)"
                            )
                    except ValueError as e:
                        failed_count += 1
                        failed_ids.append(memory_id)
                        logger.error(
                            f"[批量删除]  memory_id 格式错误 '{memory_id}': {e}"
                        )
                    except Exception as e:
                        failed_count += 1
                        failed_ids.append(memory_id)
                        logger.error(
                            f"[批量删除]  删除异常 memory_id={memory_id}: {e}",
                            exc_info=True,
                        )

                logger.info(
                    f"[批量删除] 完成 - 成功: {deleted_count}, 失败: {failed_count}, "
                    f"失败ID: {failed_ids}"
                )

                return {
                    "success": True,
                    "data": {
                        "deleted_count": deleted_count,
                        "failed_count": failed_count,
                        "total": len(memory_ids),
                        "failed_ids": failed_ids,  # 返回失败的 ID 用于客户端诊断
                    },
                }
            except Exception as e:
                logger.error(f"[批量删除] 异常: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取统计信息
        @self._app.get("/api/stats")
        async def get_stats(token: str = Depends(self._auth_dependency())):
            try:
                stats = await self.memory_engine.get_statistics()
                return {"success": True, "data": stats}
            except Exception as e:
                logger.error(f"获取统计信息失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 清理旧记忆
        @self._app.post("/api/cleanup")
        async def cleanup_memories(
            payload: Optional[Dict[str, Any]] = None,
            token: str = Depends(self._auth_dependency()),
        ):
            payload = payload or {}
            days_threshold = payload.get("days_threshold")
            importance_threshold = payload.get("importance_threshold")

            try:
                deleted_count = await self.memory_engine.cleanup_old_memories(
                    days_threshold=days_threshold,
                    importance_threshold=importance_threshold,
                )

                return {
                    "success": True,
                    "data": {
                        "deleted_count": deleted_count,
                        "message": f"已清理 {deleted_count} 条旧记忆",
                    },
                }
            except Exception as e:
                logger.error(f"清理记忆失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 召回测试 API
        @self._app.post("/api/recall/test")
        async def test_recall(
            payload: Dict[str, Any], token: str = Depends(self._auth_dependency())
        ):
            """
            测试记忆召回功能

            参数:
                query: 查询内容 (必需)
                k: 返回的记忆数量，默认 5 (可选)
                session_id: 会话 ID 过滤，支持多种格式 (可选)

            返回:
                包含召回的记忆列表、执行耗时等信息
            """
            query = payload.get("query", "").strip()
            if not query:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="查询内容不能为空"
                )

            k = min(50, max(1, int(payload.get("k", 5))))
            session_id = payload.get("session_id")  # 可选的会话过滤

            try:
                import time

                # 记录开始时间
                start_time = time.time()

                logger.info(
                    f"[召回测试] 开始执行：query='{query[:50]}...', k={k}, session_id={session_id}"
                )

                # 执行召回
                results = await self.memory_engine.search_memories(
                    query=query, k=k, session_id=session_id, persona_id=None
                )

                # 计算耗时（毫秒）
                elapsed_time = (time.time() - start_time) * 1000

                logger.info(
                    f"[召回测试] 完成：返回 {len(results)} 条结果，耗时 {elapsed_time:.2f}ms"
                )

                # 格式化结果，包含详细信息
                formatted_results = []
                for result in results:
                    formatted_results.append(
                        {
                            "memory_id": result.doc_id,
                            "content": result.content,
                            "similarity_score": round(result.final_score, 4),
                            "score_percentage": round(result.final_score * 100, 2),
                            "metadata": {
                                "session_id": result.metadata.get("session_id"),
                                "persona_id": result.metadata.get("persona_id"),
                                "importance": result.metadata.get("importance", 0.5),
                                "memory_type": result.metadata.get(
                                    "memory_type", "GENERAL"
                                ),
                                "status": result.metadata.get("status", "active"),
                                "create_time": result.metadata.get("create_time"),
                            },
                        }
                    )

                return {
                    "success": True,
                    "data": {
                        "results": formatted_results,
                        "total": len(formatted_results),
                        "query": query,
                        "k": k,
                        "session_id_filter": session_id,
                        "elapsed_time_ms": round(elapsed_time, 2),
                    },
                }
            except Exception as e:
                logger.error(f"召回测试失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取会话列表
        @self._app.get("/api/sessions")
        async def get_sessions(token: str = Depends(self._auth_dependency())):
            try:
                stats = await self.memory_engine.get_statistics()
                sessions = stats.get("sessions", {})

                # 格式化为列表
                session_list = []
                for session_id, count in sessions.items():
                    session_list.append(
                        {"session_id": session_id, "memory_count": count}
                    )

                # 按记忆数量排序
                session_list.sort(key=lambda x: x["memory_count"], reverse=True)

                return {
                    "success": True,
                    "data": {"sessions": session_list, "total": len(session_list)},
                }
            except Exception as e:
                logger.error(f"获取会话列表失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取配置信息
        @self._app.get("/api/config")
        async def get_config(token: str = Depends(self._auth_dependency())):
            try:
                # 返回安全的配置信息(不包含敏感数据)
                safe_config = {
                    "session_timeout": self.session_timeout,
                    "memory_config": {
                        "rrf_k": self.memory_engine.config.get("rrf_k", 60),
                        "decay_rate": self.memory_engine.config.get("decay_rate", 0.01),
                        "importance_weight": self.memory_engine.config.get(
                            "importance_weight", 1.0
                        ),
                        "cleanup_days_threshold": self.memory_engine.config.get(
                            "cleanup_days_threshold", 30
                        ),
                        "cleanup_importance_threshold": self.memory_engine.config.get(
                            "cleanup_importance_threshold", 0.3
                        ),
                    },
                }

                return {"success": True, "data": safe_config}
            except Exception as e:
                logger.error(f"获取配置信息失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 检查索引重建状态
        @self._app.get("/api/migration/index-status")
        async def check_index_status(token: str = Depends(self._auth_dependency())):
            """检查索引一致性状态"""
            try:
                if not self.index_validator:
                    return {
                        "success": True,
                        "data": {
                            "is_consistent": True,
                            "needs_rebuild": False,
                            "message": "索引验证器未初始化，跳过检查",
                        },
                    }

                # 检查索引一致性
                status = await self.index_validator.check_consistency()

                return {
                    "success": True,
                    "data": {
                        "is_consistent": status.is_consistent,
                        "needs_rebuild": status.needs_rebuild,
                        "documents_count": status.documents_count,
                        "bm25_count": status.bm25_count,
                        "vector_count": status.vector_count,
                        "missing_in_bm25": status.missing_in_bm25,
                        "missing_in_vector": status.missing_in_vector,
                        "message": status.reason,
                    },
                }

            except Exception as e:
                logger.error(f"检查索引状态失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 重建索引
        @self._app.post("/api/migration/rebuild-index")
        async def rebuild_index(token: str = Depends(self._auth_dependency())):
            """重建索引（使用IndexValidator）"""
            try:
                if not self.index_validator:
                    return {"success": False, "error": "索引验证器未初始化"}

                logger.info("[WebUI] 开始手动重建索引")

                # 使用IndexValidator重建索引
                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f"[WebUI] 索引重建完成 - 成功: {result['processed']}, 失败: {result['errors']}"
                    )
                    return {
                        "success": True,
                        "data": {
                            "message": f"索引重建完成！成功: {result['processed']} 条，失败: {result['errors']} 条",
                            "processed": result.get("total", result["processed"]),
                            "success_count": result["processed"],
                            "error_count": result["errors"],
                        },
                    }
                else:
                    return {
                        "success": False,
                        "error": result.get("message", "未知错误"),
                    }

            except Exception as e:
                logger.error(f"[WebUI] 索引重建失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # ==================== 会话管理 API (ConversationManager) ====================

        # 获取会话详情
        @self._app.get("/api/conversations/{session_id}")
        async def get_conversation_detail(
            session_id: str, token: str = Depends(self._auth_dependency())
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            try:
                session_info = await self.conversation_manager.get_session_info(
                    session_id
                )
                if not session_info:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")

                return {
                    "success": True,
                    "data": {
                        "session_id": session_info.session_id,
                        "platform": session_info.platform,
                        "created_at": session_info.created_at,
                        "last_active_at": session_info.last_active_at,
                        "message_count": session_info.message_count,
                        "participants": session_info.participants,
                        "metadata": session_info.metadata,
                    },
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"获取会话详情失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取会话消息列表
        @self._app.get("/api/conversations/{session_id}/messages")
        async def get_conversation_messages(
            session_id: str,
            request: Request,
            token: str = Depends(self._auth_dependency()),
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            try:
                query = request.query_params
                limit = min(200, max(1, int(query.get("limit", 50))))
                sender_id = query.get("sender_id")  # 可选的发送者过滤

                messages = await self.conversation_manager.get_messages(
                    session_id=session_id, limit=limit, sender_id=sender_id
                )

                # 格式化消息列表
                formatted_messages = [
                    {
                        "id": msg.id,
                        "role": msg.role,
                        "content": msg.content,
                        "sender_id": msg.sender_id,
                        "sender_name": msg.sender_name,
                        "group_id": msg.group_id,
                        "platform": msg.platform,
                        "timestamp": msg.timestamp,
                        "metadata": msg.metadata,
                    }
                    for msg in messages
                ]

                return {
                    "success": True,
                    "data": {"messages": formatted_messages, "total": len(messages)},
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"获取会话消息失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取会话上下文（LLM格式）
        @self._app.get("/api/conversations/{session_id}/context")
        async def get_conversation_context(
            session_id: str,
            request: Request,
            token: str = Depends(self._auth_dependency()),
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            try:
                query = request.query_params
                max_messages = int(query.get("max_messages", 50))
                sender_id = query.get("sender_id")
                format_for_llm = query.get("format_for_llm", "true").lower() == "true"

                context = await self.conversation_manager.get_context(
                    session_id=session_id,
                    max_messages=max_messages,
                    sender_id=sender_id,
                    format_for_llm=format_for_llm,
                )

                return {"success": True, "data": {"context": context}}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"获取会话上下文失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 搜索会话消息
        @self._app.post("/api/conversations/{session_id}/search")
        async def search_conversation_messages(
            session_id: str,
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            keyword = payload.get("keyword", "").strip()
            if not keyword:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="关键词不能为空"
                )

            limit = min(100, max(1, int(payload.get("limit", 20))))

            try:
                messages = await self.conversation_manager.store.search_messages(
                    session_id=session_id, keyword=keyword, limit=limit
                )

                # 格式化消息列表
                formatted_messages = [
                    {
                        "id": msg.id,
                        "role": msg.role,
                        "content": msg.content,
                        "sender_id": msg.sender_id,
                        "sender_name": msg.sender_name,
                        "timestamp": msg.timestamp,
                    }
                    for msg in messages
                ]

                return {
                    "success": True,
                    "data": {"messages": formatted_messages, "total": len(messages)},
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"搜索会话消息失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 清空会话历史
        @self._app.delete("/api/conversations/{session_id}/messages")
        async def clear_conversation_history(
            session_id: str, token: str = Depends(self._auth_dependency())
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            try:
                await self.conversation_manager.clear_session(session_id)
                return {
                    "success": True,
                    "message": f"会话 {session_id} 的历史已清空",
                }
            except Exception as e:
                logger.error(f"清空会话历史失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取最近活跃的会话
        @self._app.get("/api/conversations/recent")
        async def get_recent_conversations(
            request: Request, token: str = Depends(self._auth_dependency())
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            try:
                query = request.query_params
                limit = min(100, max(1, int(query.get("limit", 10))))

                sessions = await self.conversation_manager.get_recent_sessions(limit)

                # 格式化会话列表
                formatted_sessions = [
                    {
                        "session_id": session.session_id,
                        "platform": session.platform,
                        "created_at": session.created_at,
                        "last_active_at": session.last_active_at,
                        "message_count": session.message_count,
                        "participants": session.participants,
                    }
                    for session in sessions
                ]

                return {
                    "success": True,
                    "data": {
                        "sessions": formatted_sessions,
                        "total": len(formatted_sessions),
                    },
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"获取最近会话失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 获取会话统计信息
        @self._app.get("/api/conversations/{session_id}/stats")
        async def get_conversation_stats(
            session_id: str, token: str = Depends(self._auth_dependency())
        ):
            if not self.conversation_manager:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="会话管理功能未启用",
                )

            try:
                # 获取会话信息
                session_info = await self.conversation_manager.get_session_info(
                    session_id
                )
                if not session_info:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")

                # 获取用户消息统计
                user_stats = (
                    await self.conversation_manager.store.get_user_message_stats(
                        session_id
                    )
                )

                return {
                    "success": True,
                    "data": {
                        "session_id": session_id,
                        "total_messages": session_info.message_count,
                        "user_stats": user_stats,
                        "participants_count": len(session_info.participants),
                        "created_at": session_info.created_at,
                        "last_active_at": session_info.last_active_at,
                    },
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"获取会话统计失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
