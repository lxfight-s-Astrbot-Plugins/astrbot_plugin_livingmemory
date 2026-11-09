"""用户档案 Page API 处理模块（查看/编辑/历史/待确认候选）。"""

from typing import TYPE_CHECKING, Any

from quart import request

from astrbot.api import logger

if TYPE_CHECKING:
    from .utils import PageApiUtils


class ProfileHandler:
    """用户档案管理处理器。"""

    def __init__(self, utils: "PageApiUtils"):
        """
        初始化用户档案处理器。

        Args:
            utils: PageApiUtils 工具实例。
        """
        self.utils = utils

    @staticmethod
    def _manager(memory_engine: Any):
        """Return the profile manager; ``None`` when the engine is not ready."""
        manager = getattr(memory_engine, "profile_manager", None)
        if manager is None:
            return None
        if not callable(getattr(manager, "get_profile", None)):
            return None
        return manager

    @staticmethod
    def _parse_positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    async def list_profiles(self, memory_engine) -> dict[str, Any]:
        """列出档案；同时给出 scope+persona_id 时返回单条。

        查询参数:
            - scope: 记忆作用域（可选）
            - persona_id: 人格 ID（可选，与 scope 同时给出时返回单条）

        Returns:
            包含 profiles/total（或单条 profile）的响应。
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        scope = self.utils.optional_text(request.args.get("scope"))
        persona_raw = request.args.get("persona_id")
        persona_id = (
            self.utils.optional_text(persona_raw) if persona_raw is not None else None
        )
        try:
            if scope and persona_raw is not None:
                profile = await manager.get_profile(scope, persona_id)
                return self.utils.ok(
                    {
                        "profile": profile,
                        "profiles": [] if profile is None else [profile],
                        "total": 0 if profile is None else 1,
                    }
                )
            profiles = await manager.list_profiles(scope, persona_id)
            return self.utils.ok({"profiles": profiles, "total": len(profiles)})
        except Exception as exc:
            logger.error("获取用户档案列表失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))

    async def get_profile_detail(self, memory_engine) -> dict[str, Any]:
        """获取单个档案（含 version/updated_at/pending）。

        查询参数:
            - id: 档案 ID（必需）
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        profile_id = self._parse_positive_int(
            request.args.get("id", request.args.get("profile_id", ""))
        )
        if profile_id is None:
            return self.utils.error("id 必须是正整数")
        try:
            profile = await manager.get_profile_by_id(profile_id)
        except Exception as exc:
            logger.error("获取用户档案失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))
        if profile is None:
            return self.utils.error("用户档案不存在")
        return self.utils.ok({"profile": profile})

    async def list_profile_history(self, memory_engine) -> dict[str, Any]:
        """获取档案历史版本（新到旧）。

        查询参数:
            - id: 档案 ID（必需）
            - limit: 条数（可选，默认配置 history_limit）
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        profile_id = self._parse_positive_int(
            request.args.get("id", request.args.get("profile_id", ""))
        )
        if profile_id is None:
            return self.utils.error("id 必须是正整数")
        limit_value = request.args.get("limit")
        limit: int | None = None
        if limit_value is not None:
            limit = self._parse_positive_int(limit_value)
            if limit is None:
                return self.utils.error("limit 必须是正整数")
        try:
            history = await manager.get_history(profile_id, limit)
        except Exception as exc:
            logger.error("获取用户档案历史失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))
        return self.utils.ok(
            {"profile_id": profile_id, "history": history, "total": len(history)}
        )

    async def update_profile(self, memory_engine) -> dict[str, Any]:
        """用户编辑档案（整体替换或字段 patch），写入历史并 version+1。

        请求体:
            - id: 档案 ID（必需，也可用查询参数）
            - profile / profile_json: 完整档案文档（可选，二选一）
            - patch: ``{section: {field: value}}`` 或 ``{"section.field": value}``
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self.utils.error("请求体必须是 JSON 对象")
        profile_id = self._parse_positive_int(
            payload.get("id", request.args.get("id", ""))
        )
        if profile_id is None:
            return self.utils.error("id 必须是正整数")
        profile_json = payload.get("profile", payload.get("profile_json"))
        patch = payload.get("patch")
        if profile_json is None and patch is None:
            return self.utils.error("缺少 profile 或 patch")
        try:
            updated = await manager.update_profile_manual(
                profile_id,
                profile_json=profile_json,
                patch=patch,
                source="manual",
            )
        except ValueError as exc:
            return self.utils.error(str(exc))
        except Exception as exc:
            logger.error("更新用户档案失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))
        if updated is None:
            return self.utils.error("用户档案不存在")
        return self.utils.ok({"profile": updated})

    async def delete_profile(self, memory_engine) -> dict[str, Any]:
        """删除档案记录及其历史（硬删除，历史随档案级联删除）。

        查询参数/请求体:
            - id: 档案 ID（必需）
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        profile_id = self._parse_positive_int(
            payload.get("id", request.args.get("id", ""))
        )
        if profile_id is None:
            return self.utils.error("id 必须是正整数")
        try:
            deleted = await manager.delete_profile(profile_id)
        except Exception as exc:
            logger.error("删除用户档案失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))
        if not deleted:
            return self.utils.error("用户档案不存在")
        return self.utils.ok({"deleted": True, "id": profile_id})

    async def list_profile_pending(self, memory_engine) -> dict[str, Any]:
        """列出待确认档案候选（附 index，用于 accept/reject）。

        查询参数:
            - scope: 记忆作用域（必需）
            - persona_id: 人格 ID（可选）
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        scope = self.utils.optional_text(request.args.get("scope"))
        persona_raw = request.args.get("persona_id")
        persona_id = (
            self.utils.optional_text(persona_raw) if persona_raw is not None else None
        )
        if not scope:
            return self.utils.error("scope 不能为空")
        try:
            pending = await manager.list_pending(scope, persona_id)
        except Exception as exc:
            logger.error("获取用户档案待确认候选失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))
        if pending is None:
            return self.utils.ok({"profile": None, "pending": [], "total": 0})
        return self.utils.ok(
            {
                "profile_id": pending["id"],
                "scope": pending["scope"],
                "persona_id": pending["persona_id"],
                "version": pending["version"],
                "pending": pending["pending"],
                "total": len(pending["pending"]),
            }
        )

    async def resolve_profile_pending(self, memory_engine) -> dict[str, Any]:
        """接受或拒绝一条待确认候选。

        请求体:
            - scope: 记忆作用域（必需）
            - persona_id: 人格 ID（可选）
            - index: 候选下标（必需，从 0 开始）
            - action: ``accept`` 或 ``reject``（必需）
        """
        manager = self._manager(memory_engine)
        if manager is None:
            return self.utils.error("用户档案功能未启用")
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self.utils.error("请求体必须是 JSON 对象")
        scope = self.utils.optional_text(payload.get("scope"))
        if not scope:
            return self.utils.error("scope 不能为空")
        persona_raw = payload.get("persona_id")
        persona_id = (
            self.utils.optional_text(persona_raw) if persona_raw is not None else None
        )
        index = payload.get("index")
        if isinstance(index, bool):
            return self.utils.error("index 必须是整数")
        try:
            index = int(index)
        except (TypeError, ValueError):
            return self.utils.error("index 必须是整数")
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"accept", "reject"}:
            return self.utils.error("action 必须是 accept 或 reject")
        try:
            updated = await manager.resolve_pending(
                scope, persona_id, index, accept=action == "accept"
            )
        except ValueError as exc:
            return self.utils.error(str(exc))
        except Exception as exc:
            logger.error("处理用户档案待确认候选失败: %s", exc, exc_info=True)
            return self.utils.error(str(exc))
        if updated is None:
            return self.utils.error("用户档案不存在")
        return self.utils.ok({"profile": updated})


__all__ = ["ProfileHandler"]
