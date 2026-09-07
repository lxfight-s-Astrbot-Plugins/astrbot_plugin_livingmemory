"""
对话历史对账：WebUI 编辑/重试回滚检测（纯函数，无 IO）

AstrBot WebUI 的「编辑上一条消息并重新请求」「换模型重试上一条对话」会在
LLM 侧截断/删除对应轮次（conv_mgr 历史），这些操作对插件不可见。插件会话库
里残留的被撤销轮次（旧用户消息 + 旧 Bot 回复）会被后续总结写入长期记忆，
造成记忆污染。本模块把 AstrBot LLM 历史（req.contexts 中的 _checkpoint 段）
与插件会话库的存储轮次做尾对齐，识别出"被回滚的尾部轮次"。

关键事实：
- AstrBot 会话历史每个轮次末尾有 `{"role": "_checkpoint", "content": {"id": ...}}`
  段，`on_llm_request` 钩子时 req.contexts 已包含这些段、不含当前消息；
- webchat 每次请求生成新的 llm_checkpoint_id（编辑/重试也是新值），因此
  同一轮次在重试前后是两个不同的 checkpoint id；
- AstrBot 超长对话会从头部压缩历史（前端截断），因此只能做"尾部对齐"：
  头部缺失 = 上下文压缩（保留），尾部缺失 = 被回滚（删除）；
- 插件会话库可能缺少某些轮次的 user 行（如纯图片消息实际内容为空不入库，
  但 LLM 历史仍记录该轮与 checkpoint）——这种"store 缺行"不阻塞对账：
  ctx 中在 store 里没有锚点的 checkpoint 向前回溯跳过即可（它们不是被回滚
  的轮次，因为其 id 仍存在于 LLM 历史中）。
"""

from __future__ import annotations

from typing import Any


def _row_checkpoint(row: dict[str, Any]) -> str | None:
    """提取消息行的 checkpoint id（metadata.llm_checkpoint_id）。"""
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return None
    checkpoint = metadata.get("llm_checkpoint_id")
    return checkpoint if isinstance(checkpoint, str) and checkpoint else None


def _row_role(row: dict[str, Any]) -> str:
    return str(row.get("role") or "")


def _rows_have_checkpoints(rows: list[dict[str, Any]]) -> bool:
    """是否所有行都带 checkpoint 标记（遗留数据行不带，需保守放弃）。"""
    for row in rows:
        if not _row_checkpoint(row):
            return False
    return True


def compute_revert_cutoff(
    store_rows: list[dict[str, Any]],
    ctx_checkpoints: list[str],
) -> int | None:
    """计算插件会话库中需要删除的"被回滚尾部"的起始位置。

    Args:
        store_rows: 插件会话库消息行（按插入顺序），每行至少含
            ``role`` 与 ``metadata.llm_checkpoint_id``
        ctx_checkpoints: AstrBot LLM 历史中 ``_checkpoint`` 段的 id 有序列表，
            注意不含当前请求的消息轮次

    Returns:
        0-based 位置：该位置及之后的消息应删除（0 表示删除全部）；
        ``None`` 表示无需删除或无法安全判定（保守不删）。
    """
    if not store_rows:
        return None

    # 边界：LLM 历史为空（首次对话整轮被重试，或 AstrBot 侧会话被清空）。
    # 与 LLM 可见历史保持一致 → 删除全部；但要求所有行都带 checkpoint
    # 标记（遗留数据保守放弃，避免误删）。
    if not ctx_checkpoints:
        if not _rows_have_checkpoints(store_rows):
            return None
        return 0

    ctx_set = set(ctx_checkpoints)
    ctx_idx = len(ctx_checkpoints) - 1
    deletion_start: int | None = None

    # 从尾部向头部走：anchor 是 user 消息的 checkpoint；
    # assistant 行只参与删除区域，不参与匹配。
    i = len(store_rows) - 1
    while i >= 0:
        row = store_rows[i]
        checkpoint = _row_checkpoint(row)
        role = _row_role(row)

        if role == "user":
            if not checkpoint:
                # 匹配段内遇到遗留数据（无 checkpoint），无法判定 → 保守放弃
                return None
            if ctx_idx >= 0 and checkpoint == ctx_checkpoints[ctx_idx]:
                # 该轮次在 LLM 历史中存在
                ctx_idx -= 1
            elif checkpoint in ctx_set:
                # checkpoint 存在于 LLM 历史，但不在当前对齐位置：说明 store
                # 缺少某些轮次的对应行（如纯图片消息未入库、插件曾被禁用），
                # 向前回溯该 checkpoint 继续匹配，不阻塞对账；
                # 只有彻底找不到才视为歧义（保守放弃）
                found = -1
                for j in range(ctx_idx, -1, -1):
                    if ctx_checkpoints[j] == checkpoint:
                        found = j
                        break
                if found < 0:
                    return None
                ctx_idx = found - 1
            else:
                # 不在 LLM 历史中 → 被回滚的轮次，记入待删除区域
                if deletion_start is None or i < deletion_start:
                    deletion_start = i
        else:
            if deletion_start is None and not checkpoint:
                # 匹配段内的非 user 行缺 checkpoint（遗留数据）→ 保守放弃
                return None
            if deletion_start is None and checkpoint and checkpoint not in ctx_set:
                # 孤立回复（如被取代轮次的迟到 Bot 回复），一并清理
                deletion_start = i

        # ctx 序列已耗尽：剩余 store 头部轮次对应"上下文压缩"保留，不再处理
        if ctx_idx < 0:
            break
        i -= 1

    return deletion_start
