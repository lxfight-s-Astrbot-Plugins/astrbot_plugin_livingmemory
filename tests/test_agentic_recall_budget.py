"""Beta 自主回忆请求局部预算的测试。"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from astrbot_plugin_livingmemory.core.tools.agentic_recall_budget import (
    RecallBudget,
    charge_elapsed,
    get_or_create_budget,
    run_within_budget,
    try_consume_call,
)


def _config(**overrides):
    defaults = {
        "agent_tools.agentic_recall_max_calls": 4,
        "agent_tools.agentic_recall_time_budget_seconds": 20,
        "agent_tools.agentic_recall_max_result_chars": 12000,
    }
    defaults.update(overrides)
    return SimpleNamespace(get=lambda key, default=None: defaults.get(key, default))


class TestBudgetLifecycle:
    def test_budget_attached_to_event_and_reused(self):
        event = SimpleNamespace()
        config = _config()
        b1 = get_or_create_budget(event, config)
        b2 = get_or_create_budget(event, config)
        assert b1 is b2

    def test_budget_isolated_between_events(self):
        config = _config()
        b1 = get_or_create_budget(SimpleNamespace(), config)
        b2 = get_or_create_budget(SimpleNamespace(), config)
        assert b1 is not b2
        try_consume_call(b1)
        assert b2.calls_used == 0

    def test_defaults_when_config_missing(self):
        config = SimpleNamespace(get=lambda key, default=None: default)
        budget = get_or_create_budget(SimpleNamespace(), config)
        assert budget.max_calls == 4
        assert budget.time_budget_seconds == 20.0
        assert budget.max_result_chars == 12000

    def test_config_values_respected(self):
        config = _config(
            **{
                "agent_tools.agentic_recall_max_calls": 6,
                "agent_tools.agentic_recall_time_budget_seconds": 30,
                "agent_tools.agentic_recall_max_result_chars": 5000,
            }
        )
        budget = get_or_create_budget(SimpleNamespace(), config)
        assert budget.max_calls == 6
        assert budget.time_budget_seconds == 30.0
        assert budget.max_result_chars == 5000

    def test_invalid_config_falls_back(self):
        config = _config(**{"agent_tools.agentic_recall_max_calls": "bad"})
        budget = get_or_create_budget(SimpleNamespace(), config)
        assert budget.max_calls == 4


class TestCallBudget:
    def test_consume_until_exhausted(self):
        budget = RecallBudget(max_calls=2, time_budget_seconds=20, max_result_chars=100)
        assert try_consume_call(budget) is True
        assert try_consume_call(budget) is True
        assert try_consume_call(budget) is False
        assert budget.calls_exhausted
        assert budget.remaining_calls == 0


class TestTimeBudget:
    def test_only_actual_execution_time_accumulates(self):
        """两次调用之间经过的（模型思考）时间不计入预算。"""
        budget = RecallBudget(max_calls=4, time_budget_seconds=1, max_result_chars=100)
        charge_elapsed(budget, 0.1)
        # 模拟模型思考 5 秒：不调用 charge_elapsed
        time.sleep(0.01)
        charge_elapsed(budget, 0.1)
        assert budget.elapsed_seconds == pytest.approx(0.2)
        assert budget.remaining_seconds == pytest.approx(0.8)

    def test_time_exhausted_flag(self):
        budget = RecallBudget(max_calls=4, time_budget_seconds=0.5, max_result_chars=100)
        assert not budget.time_exhausted
        charge_elapsed(budget, 0.6)
        assert budget.time_exhausted
        assert budget.remaining_seconds == 0.0

    @pytest.mark.asyncio
    async def test_run_within_budget_success_charges_actual_time(self):
        budget = RecallBudget(max_calls=4, time_budget_seconds=5, max_result_chars=100)

        async def work():
            await asyncio.sleep(0.02)
            return "done"

        result, elapsed, timed_out = await run_within_budget(budget, work)
        assert result == "done"
        assert not timed_out
        assert elapsed >= 0.02
        # 耗时已在内部累计，调用方无需再次 charge
        assert budget.elapsed_seconds == pytest.approx(elapsed, abs=0.01)

    @pytest.mark.asyncio
    async def test_run_within_budget_timeout_cancels_work(self):
        budget = RecallBudget(max_calls=4, time_budget_seconds=0.05, max_result_chars=100)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        result, elapsed, timed_out = await run_within_budget(budget, work)
        assert result is None
        assert timed_out
        assert elapsed <= 0.2
        assert cancelled.is_set()
        # 超时路径同样累计实际耗时
        assert budget.elapsed_seconds > 0
        assert budget.time_exhausted

    @pytest.mark.asyncio
    async def test_run_within_budget_exception_charges_and_reraises(self):
        """普通异常：累计实际耗时后原样抛出，由调用方转成结构化状态。"""
        budget = RecallBudget(max_calls=4, time_budget_seconds=5, max_result_chars=100)

        async def work():
            await asyncio.sleep(0.01)
            raise RuntimeError("engine exploded")

        with pytest.raises(RuntimeError):
            await run_within_budget(budget, work)
        assert budget.elapsed_seconds >= 0.01

    @pytest.mark.asyncio
    async def test_run_within_budget_zero_remaining(self):
        budget = RecallBudget(max_calls=4, time_budget_seconds=0.1, max_result_chars=100)
        charge_elapsed(budget, 1.0)
        created = False

        def factory():
            nonlocal created
            created = True

            async def work():
                return None

            return work()

        result, _, timed_out = await run_within_budget(budget, factory)
        assert result is None and timed_out
        # 预算耗尽时不创建协程，避免 "coroutine was never awaited" 警告
        assert created is False

    def test_status_shape(self):
        budget = RecallBudget(max_calls=3, time_budget_seconds=10, max_result_chars=100)
        status = budget.to_status()
        assert status["remaining_calls"] == 3
        assert status["remaining_seconds"] == 10.0
