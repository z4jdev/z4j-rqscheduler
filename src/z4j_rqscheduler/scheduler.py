"""Native RQ inventory and transactional dashboard control.

Managed producers preserve pause state and arbitrate edits against dispatch.
External changes are discovered on the configured periodic reconciliation cadence."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from z4j_core.errors import NotFoundError
from z4j_core.models import CommandResult, Schedule, ScheduleKind
from z4j_core.native_schedule_operations import direct_native_mutation
from z4j_core.schedule_external import (
    EXTERNAL_SCHEDULE_MUTATION_CAPABILITY,
    EXTERNAL_SCHEDULE_STABLE_SNAPSHOT_CAPABILITY,
)

from z4j_rqscheduler._offload import (
    OffloadTimeoutError,
    indeterminate_timeout_result,
    offload,
)
from z4j_rqscheduler.capabilities import DEFAULT_CAPABILITIES
from z4j_rqscheduler.native import NativeRqControl

logger = logging.getLogger("z4j.adapter.rqscheduler.scheduler")

_NAME = "rq-scheduler"
_ENGINE = "rq"


class RqSchedulerAdapter:
    """Scheduler adapter for rq-scheduler.

    Args:
        scheduler: A live ``rq_scheduler.Scheduler`` instance, OR a
                   duck-typed object with ``get_jobs()`` +
                   ``cancel(job)`` + ``enqueue_in(td, ...)``.
                   Tests pass a minimal fake; production passes the
                   real ``rq_scheduler.Scheduler``.
        project_id: Optional project id used when constructing
                    :class:`Schedule` instances. If omitted the
                    brain assigns one from the agent's project
                    membership.
    """

    name: str = _NAME

    def __init__(
        self,
        *,
        scheduler: Any,
        project_id: UUID | None = None,
        managed: bool = False,
        tasks: dict[str, Any] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self._project_id = project_id or uuid4()
        from rq_scheduler import Scheduler

        self._native = (
            NativeRqControl(scheduler, self._project_id, managed=managed, tasks=dict(tasks or {}))
            if type(scheduler) is Scheduler
            else None
        )
        if managed and self._native is None:
            raise ValueError("managed native control requires rq_scheduler.Scheduler")

    # ------------------------------------------------------------------
    # Lifecycle - rq-scheduler doesn't emit registry-change events
    # (no Django signal equivalent). The agent runtime's scheduler
    # sink is still invoked once at startup via ``list_schedules()``
    # projection; no per-change signal is fired.
    # ------------------------------------------------------------------

    def connect_signals(self, sink: Any) -> None:
        """rq-scheduler has no change-signal story - no-op.

        The agent runtime calls :meth:`list_schedules` at startup, on its
        configured periodic reconciliation cadence, and on an explicit
        resync command.
        """
        return

    def disconnect_signals(self) -> None:
        return

    # ------------------------------------------------------------------
    # SchedulerAdapter - read
    # ------------------------------------------------------------------

    async def list_schedules(self) -> list[Schedule]:
        if self._native is not None:
            return await offload(self._native.snapshot, timeout=10)
        # RM6: get_jobs() is synchronous redis-py I/O; offload it so a slow
        # Redis cannot freeze the agent loop (matches get_schedule). A timeout
        # or broker error PROPAGATES rather than returning [] -- an empty list
        # would let a reconcile delete every schedule (RM5). A per-job mapping
        # error also aborts the whole snapshot: omitting one job would make the
        # authoritative reconcile delete exactly that still-live schedule.
        jobs = await offload(_list_jobs, self.scheduler, timeout=10.0)
        out: list[Schedule] = []
        for job in jobs:
            try:
                out.append(self._to_schedule(job))
            except Exception:
                logger.exception(
                    "z4j rq-scheduler: failed to map job %r; skipping this authoritative snapshot",
                    getattr(job, "id", "?"),
                )
                raise
        return out

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        if self._native is not None:
            return next(
                (
                    row
                    for row in await self.list_schedules()
                    if schedule_id in {str(row.id), row.external_id}
                ),
                None,
            )
        # M15: get_jobs() is a synchronous redis-py zrange plus one Job.fetch
        # per scheduled job. trigger_now() calls this BEFORE its own offloaded
        # section, so leaving the scan inline on the event loop reintroduced
        # the exact loop-freeze on a hung/slow Redis that the offload exists
        # to prevent (heartbeats, command polling, and every other adapter
        # would stall at the lookup, before the 10s timeout could ever fire).
        # Offload the scan under the same timeout.
        try:
            jobs = await offload(_list_jobs, self.scheduler, timeout=10.0)
        except Exception:
            # OffloadTimeoutError (a slow Redis) and any broker error both
            # resolve to "schedule not resolvable right now" -> None.
            return None
        for job in jobs:
            if _safe_str(getattr(job, "id", "")) == schedule_id:
                try:
                    return self._to_schedule(job)
                except Exception:
                    return None
        return None

    # ------------------------------------------------------------------
    # SchedulerAdapter - write
    # ------------------------------------------------------------------

    async def create_schedule(self, spec: Schedule) -> Schedule:
        if self._native is not None and self._native.managed:
            result = await direct_native_mutation(self, "create", None, spec)
            assert isinstance(result, Schedule)
            return result
        # Not supported - capabilities() omits "create" so the
        # dashboard hides the button. If the brain bypasses the gate
        # we fail loudly.
        raise NotImplementedError(
            "z4j-rqscheduler does not create schedules remotely; "
            "create schedules via your project's Python entrypoint "
            "(scheduler.cron(...) / scheduler.schedule(...)).",
        )

    async def update_schedule(
        self,
        schedule_id: str,
        spec: Schedule,
    ) -> Schedule:
        if self._native is not None and self._native.managed:
            result = await direct_native_mutation(self, "update", schedule_id, spec)
            assert isinstance(result, Schedule)
            return result
        raise NotImplementedError(
            "z4j-rqscheduler does not update schedules remotely; "
            "edit the definition in your project's Python entrypoint.",
        )

    async def delete_schedule(self, schedule_id: str) -> CommandResult:  # noqa: PLR0911 - native and compatibility error paths
        if self._native is not None and self._native.managed:
            result = await direct_native_mutation(self, "delete", schedule_id)
            assert isinstance(result, CommandResult)
            return result
        # M4: get_jobs() (read) and cancel() (mutation) are synchronous
        # redis-py calls; offload both so a slow Redis cannot freeze the agent
        # loop. A read timeout is a clean, retryable failure; a cancel timeout
        # is indeterminate (the cancel may still have landed).
        try:
            # RM3: offload(get_jobs) returns rq-scheduler's LAZY generator;
            # list()-ing it here drove one redis-py Job.fetch per job back on
            # the event loop, defeating the offload. _list_jobs materialises
            # the full list INSIDE the worker thread (same helper get_schedule
            # / list_schedules use).
            jobs = await offload(_list_jobs, self.scheduler, timeout=10.0)
        except OffloadTimeoutError:
            return CommandResult(
                status="failed",
                error="get_jobs timed out reading the schedule set (safe to retry)",
            )
        except Exception as exc:
            return CommandResult(
                status="failed",
                error=f"get_jobs failed: {exc}",
            )
        target = None
        for job in jobs:
            if _safe_str(getattr(job, "id", "")) == schedule_id:
                target = job
                break
        if target is None:
            # Idempotent - deleting a missing schedule is a success
            # per SchedulerAdapter.delete_schedule contract.
            return CommandResult(
                status="success",
                result={"schedule_id": schedule_id, "noop": True},
            )
        try:
            await offload(self.scheduler.cancel, target, timeout=10.0)
        except OffloadTimeoutError:
            return indeterminate_timeout_result(
                "delete_schedule", 10.0, hint="the cancel may still have landed"
            )
        except Exception as exc:
            return CommandResult(
                status="failed",
                error=f"cancel failed: {exc}",
            )
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id},
        )

    async def enable_schedule(self, schedule_id: str) -> CommandResult:
        if self._native is not None and self._native.managed:
            result = await direct_native_mutation(self, "update", schedule_id, enabled=True)
            assert isinstance(result, CommandResult)
            return result
        # rq-scheduler doesn't have a first-class enabled/disabled
        # flag - disabling means "cancel the scheduled job". Enable
        # therefore only makes sense if the underlying job is still
        # in the store; we surface that cleanly.
        sched = await self.get_schedule(schedule_id)
        if sched is None:
            return CommandResult(
                status="failed",
                error=f"schedule {schedule_id!r} not found",
            )
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id, "is_enabled": True},
        )

    async def disable_schedule(self, schedule_id: str) -> CommandResult:
        if self._native is not None and self._native.managed:
            result = await direct_native_mutation(self, "update", schedule_id, enabled=False)
            assert isinstance(result, CommandResult)
            return result
        # Same disclaimer as enable - disable maps to delete in
        # rq-scheduler. We preserve the original call semantics and
        # let the brain decide whether to surface this as disabled
        # vs gone.
        return await self.delete_schedule(schedule_id)

    async def trigger_now(self, schedule_id: str) -> CommandResult:
        if self._native is not None:
            try:
                task_id = await offload(self._native.trigger, schedule_id, timeout=10)
                return CommandResult(status="success", result={"task_id": task_id})
            except OffloadTimeoutError:
                return indeterminate_timeout_result(
                    "trigger_now", 10, hint="inspect the queue before retrying"
                )
        sched = await self.get_schedule(schedule_id)
        if sched is None:
            raise NotFoundError(f"schedule {schedule_id!r} not found")

        # get_jobs() + enqueue_in() are synchronous redis-py calls; run them
        # on the dedicated broker-offload pool under a timeout so a slow /
        # failing Redis cannot freeze the agent event loop OR starve its
        # heartbeat providers (isolated from the default executor).
        try:
            new_job = await offload(
                _sync_trigger_now,
                self.scheduler,
                schedule_id,
                timeout=10.0,
            )
        except OffloadTimeoutError:
            # The enqueue may still have reached Redis; report
            # indeterminate rather than a clean failure.
            return indeterminate_timeout_result(
                "trigger_now",
                10.0,
                hint="the job may still have been enqueued",
            )
        except Exception as exc:
            return CommandResult(
                status="failed",
                error=f"trigger_now failed: {exc}",
            )
        if new_job is None:
            return CommandResult(
                status="failed",
                error=f"schedule {schedule_id!r} vanished between lookup and trigger",
            )
        return CommandResult(
            status="success",
            result={
                "schedule_id": schedule_id,
                "task_id": _safe_str(getattr(new_job, "id", "")),
            },
        )

    # ------------------------------------------------------------------
    # SchedulerAdapter - capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> set[str]:
        caps = set(DEFAULT_CAPABILITIES)
        if self._native is not None:
            caps.discard("disable")
            caps.add(EXTERNAL_SCHEDULE_STABLE_SNAPSHOT_CAPABILITY)
            if self._native.managed:
                caps.update(
                    {EXTERNAL_SCHEDULE_MUTATION_CAPABILITY, "create", "update", "enable", "disable"}
                )
        return caps

    async def apply_external_mutation(
        self,
        *,
        operation: str,
        schedule_id: str | None,
        expected: dict[str, Any] | None,
        definition: dict[str, Any],
    ) -> Schedule | None:
        """Apply one Redis compare-and-set with the cooperating RQ producer."""
        if self._native is None:
            raise ValueError("native RQ control requires a real rq_scheduler.Scheduler")
        return await offload(
            self._native.mutate,
            operation=operation,
            schedule_id=schedule_id,
            expected=expected,
            definition=definition,
            timeout=10,
        )

    # ------------------------------------------------------------------
    # Internal: Job → Schedule projection
    # ------------------------------------------------------------------

    def _to_schedule(self, job: Any) -> Schedule:
        now = datetime.now(UTC)
        sched_id = _safe_str(getattr(job, "id", "")) or str(uuid4())
        cron_expr = _safe_str(
            getattr(job, "meta", {}).get("cron_string", "")
            if isinstance(
                getattr(job, "meta", None),
                dict,
            )
            else "",
        )
        if cron_expr:
            kind = ScheduleKind.CRON
            expression = cron_expr
        else:
            interval = (
                getattr(job, "meta", {}).get("interval")
                if isinstance(
                    getattr(job, "meta", None),
                    dict,
                )
                else None
            )
            if interval:
                kind = ScheduleKind.INTERVAL
                expression = _safe_str(interval)
            else:
                # Fall back to clocked-at the job's scheduled_for.
                kind = ScheduleKind.CLOCKED
                scheduled_for = getattr(job, "scheduled_for", None)
                expression = (
                    scheduled_for.isoformat() if isinstance(scheduled_for, datetime) else "unknown"
                )

        return Schedule(
            id=_safe_uuid(sched_id),
            project_id=self._project_id,
            engine=_ENGINE,
            scheduler=_NAME,
            name=_safe_str(getattr(job, "func_name", sched_id))[:200],
            task_name=_safe_str(getattr(job, "func_name", sched_id))[:500],
            kind=kind,
            expression=expression[:200] or "unknown",
            timezone="UTC",
            queue=_safe_str(getattr(job, "origin", "default")) or None,
            args=list(getattr(job, "args", []) or []),
            kwargs=dict(getattr(job, "kwargs", {}) or {}),
            is_enabled=True,
            total_runs=0,
            external_id=sched_id,
            metadata={},
            created_at=now,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_jobs(scheduler: Any) -> list[Any]:
    """Synchronous ``list(scheduler.get_jobs())`` -- runs on the offload pool.

    M15: kept as a top-level function (not an inline lambda) so it is a clean
    picklable-free target for the executor and reads the same in every caller.
    """
    return list(scheduler.get_jobs())


def _sync_trigger_now(scheduler: Any, schedule_id: str) -> Any | None:
    """Synchronous re-enqueue of a scheduled RQ job (runs in an executor
    thread; see ``trigger_now``). Returns the new job, or None if the
    schedule vanished between lookup and trigger."""
    from datetime import timedelta

    for job in scheduler.get_jobs():
        if _safe_str(getattr(job, "id", "")) == schedule_id:
            return scheduler.enqueue_in(
                timedelta(0),
                job.func_name,
                *list(getattr(job, "args", [])),
                **dict(getattr(job, "kwargs", {})),
            )
    return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_uuid(value: str) -> UUID:
    """Deterministic UUID5 from a string id so the brain can dedupe."""
    try:
        return UUID(value)
    except Exception:
        import uuid as _uuid

        return _uuid.uuid5(_uuid.NAMESPACE_OID, value)


__all__ = ["RqSchedulerAdapter"]
