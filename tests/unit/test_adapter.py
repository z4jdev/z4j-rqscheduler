"""Tests for :class:`RqSchedulerAdapter`."""

from __future__ import annotations

import pytest

from z4j_core.models import ScheduleKind
from z4j_core.protocols import SchedulerAdapter

from z4j_rqscheduler import RqSchedulerAdapter
from z4j_rqscheduler.capabilities import DEFAULT_CAPABILITIES


class TestProtocolConformance:
    def test_satisfies_scheduler_adapter_protocol(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        assert isinstance(adapter, SchedulerAdapter)

    def test_name_is_rq_scheduler(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        assert adapter.name == "rq-scheduler"


class TestCapabilities:
    def test_frozen_set(self):
        assert DEFAULT_CAPABILITIES == frozenset(
            {"list", "enable", "disable", "trigger_now", "delete"},
        )

    def test_create_update_absent(self):
        assert "create" not in DEFAULT_CAPABILITIES
        assert "update" not in DEFAULT_CAPABILITIES


class TestList:
    @pytest.mark.asyncio
    async def test_lists_all_jobs(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        assert len(items) == 2
        assert {s.name for s in items} == {
            "myapp.tasks.nightly", "myapp.tasks.refresh",
        }

    @pytest.mark.asyncio
    async def test_cron_kind_detected(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        cron = next(s for s in items if s.name == "myapp.tasks.nightly")
        assert cron.kind == ScheduleKind.CRON
        assert cron.expression == "0 3 * * *"

    @pytest.mark.asyncio
    async def test_interval_kind_detected(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        inter = next(s for s in items if s.name == "myapp.tasks.refresh")
        assert inter.kind == ScheduleKind.INTERVAL
        assert inter.expression == "60"

    @pytest.mark.asyncio
    async def test_engine_name_stamped(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        items = await adapter.list_schedules()
        for s in items:
            assert s.engine == "rq"
            assert s.scheduler == "rq-scheduler"


class TestDelete:
    @pytest.mark.asyncio
    async def test_cancels_matching_job(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        result = await adapter.delete_schedule("job-cron-1")
        assert result.status == "success"
        assert "job-cron-1" in scheduler.cancelled

    @pytest.mark.asyncio
    async def test_missing_id_is_noop_success(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        result = await adapter.delete_schedule("ghost")
        assert result.status == "success"
        assert result.result["noop"] is True


class TestTriggerNow:
    @pytest.mark.asyncio
    async def test_enqueues_immediate_copy(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        result = await adapter.trigger_now("job-cron-1")
        assert result.status == "success"
        assert result.result["task_id"].startswith("new-")
        # Verify td was zero
        assert scheduler.enqueued[-1]["td_s"] == 0
        assert scheduler.enqueued[-1]["func"] == "myapp.tasks.nightly"

    @pytest.mark.asyncio
    async def test_missing_id_raises_notfound(self, scheduler):
        from z4j_core.errors import NotFoundError
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        with pytest.raises(NotFoundError):
            await adapter.trigger_now("ghost")


class TestEnableDisable:
    @pytest.mark.asyncio
    async def test_disable_delegates_to_delete(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        result = await adapter.disable_schedule("job-cron-1")
        assert result.status == "success"
        assert "job-cron-1" in scheduler.cancelled

    @pytest.mark.asyncio
    async def test_enable_succeeds_when_job_present(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        result = await adapter.enable_schedule("job-cron-1")
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_enable_missing_job_fails(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        result = await adapter.enable_schedule("ghost")
        assert result.status == "failed"


class TestCreateUpdateDeferred:
    @pytest.mark.asyncio
    async def test_create_raises_notimplemented(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        with pytest.raises(NotImplementedError):
            await adapter.create_schedule(None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_update_raises_notimplemented(self, scheduler):
        adapter = RqSchedulerAdapter(scheduler=scheduler)
        with pytest.raises(NotImplementedError):
            await adapter.update_schedule("any-id", None)  # type: ignore[arg-type]
