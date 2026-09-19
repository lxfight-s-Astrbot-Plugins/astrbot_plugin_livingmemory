"""Beta 自主回忆的请求局部预算。

预算挂在事件对象上，同一用户请求内的多次记忆工具调用共享一份预算，
不同请求之间互不影响。耗时只累计工具实际执行时间（用单调时钟包住
引擎调用），模型思考、工具往返、参数补问期间不扣预算。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

# 事件对象上承载预算的私有属性名
_BUDGET_ATTR = "_livingmemory_agentic_recall_budget"

DEFAULT_MAX_CALLS = 4
DEFAULT_TIME_BUDGET_SECONDS = 20
DEFAULT_MAX_RESULT_CHARS = 12000


@dataclass
class RecallBudget:
    """单请求共享的记忆工具预算。"""

    max_calls: int
    time_budget_seconds: float
    max_result_chars: int
    calls_used: int = 0
    elapsed_seconds: float = 0.0

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.time_budget_seconds - self.elapsed_seconds)

    @property
    def calls_exhausted(self) -> bool:
        return self.calls_used >= self.max_calls

    @property
    def time_exhausted(self) -> bool:
        return self.remaining_seconds <= 0.0

    def to_status(self) -> dict[str, Any]:
        return {
            "remaining_calls": self.remaining_calls,
            "remaining_seconds": round(self.remaining_seconds, 3),
        }


def _read_int(config: Any, key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def get_or_create_budget(event: Any, config: Any) -> RecallBudget:
    """获取当前请求的记忆工具预算；不存在时按配置惰性创建。"""
    budget = getattr(event, _BUDGET_ATTR, None)
    if isinstance(budget, RecallBudget):
        return budget
    budget = RecallBudget(
        max_calls=max(
            1,
            _read_int(config, "agent_tools.agentic_recall_max_calls", DEFAULT_MAX_CALLS),
        ),
        time_budget_seconds=float(
            max(
                1,
                _read_int(
                    config,
                    "agent_tools.agentic_recall_time_budget_seconds",
                    DEFAULT_TIME_BUDGET_SECONDS,
                ),
            )
        ),
        max_result_chars=max(
            500,
            _read_int(
                config,
                "agent_tools.agentic_recall_max_result_chars",
                DEFAULT_MAX_RESULT_CHARS,
            ),
        ),
    )
    setattr(event, _BUDGET_ATTR, budget)
    return budget


def try_consume_call(budget: RecallBudget) -> bool:
    """占用一次记忆工具调用名额；次数耗尽时返回 False。"""
    if budget.calls_used >= budget.max_calls:
        return False
    budget.calls_used += 1
    return True


def charge_elapsed(budget: RecallBudget, seconds: float) -> None:
    """累计本次工具调用实际执行耗时。"""
    if seconds > 0:
        budget.elapsed_seconds += seconds


async def run_within_budget(
    budget: RecallBudget, factory: Callable[[], Any]
) -> tuple[Any, float, bool]:
    """在剩余时间预算内执行异步工作。

    使用工厂函数惰性创建协程，避免预算已耗尽时产生未 await 的警告。
    无论成功、超时、抛错还是外部取消，都会累计实际执行耗时后原样传播。

    Returns:
        (结果或 None, 实际执行秒数, 是否因预算超时)。超时后内部任务已被取消。
    """
    remaining = budget.remaining_seconds
    if remaining <= 0:
        return None, 0.0, True
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(factory(), timeout=remaining)
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        charge_elapsed(budget, elapsed)
        return None, elapsed, True
    except asyncio.CancelledError:
        charge_elapsed(budget, time.monotonic() - start)
        raise
    except Exception:
        charge_elapsed(budget, time.monotonic() - start)
        raise
    elapsed = time.monotonic() - start
    charge_elapsed(budget, elapsed)
    return result, elapsed, False
