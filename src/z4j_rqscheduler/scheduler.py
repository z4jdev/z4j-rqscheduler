"""The :class:`RqSchedulerAdapter` - SchedulerAdapter for rq-scheduler.

Implements :class:`z4j_core.protocols.SchedulerAdapter` on top of
the `rq-scheduler`_ library. v1 surface is read-heavy:

- ``list`` - walk the `rq:scheduler:scheduled_jobs` Redis zset
- ``get_schedule`` - fetch a single scheduled Job by id
- ``enable`` / ``disable`` - toggle via the scheduler's
  ``enqueue_in(0, ...)`` pause/resume pattern
- ``trigger_now`` - enqueue the job immediately via
  ``scheduler.enqueue_in(timedelta(0), ...)``
- ``delete`` - ``scheduler.cancel(job)``

Write surface (``create``, ``update``) is deferred to v1.1 - the
dashboard's schedule-creation UX lives on the Celery track first.

.. _rq-scheduler: https://github.com/rq/rq-scheduler
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from z4j_core.errors import NotFoundError
from z4j_core.models import CommandResult, Schedule, ScheduleKind

from z4j_rqscheduler.capabilities import DEFAULT_CAPABILITIES

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
    ) -> None:
        self.scheduler = scheduler
        self._project_id = project_id or uuid4()

    # ------------------------------------------------------------------
    # Lifecycle - rq-scheduler doesn't emit registry-change events
    # (no Django signal equivalent). The agent runtime's scheduler
    # sink is still invoked once at startup via ``list_schedules()``
    # projection; no per-change signal is fired.
    # ------------------------------------------------------------------

    def connect_signals(self, sink: Any) -> None:  # noqa: ARG002  (no-op)
        """rq-scheduler has no change-signal story - no-op.

        The runtime's periodic reconciliation (Phase 1.1) will call
        :meth:`list_schedules` to refresh the brain's snapshot.
        """
        return

    def disconnect_signals(self) -> None:
        return

    # ------------------------------------------------------------------
    # SchedulerAdapter - read
    # ------------------------------------------------------------------

    async def list_schedules(self) -> list[Schedule]:
        try:
            jobs = list(self.scheduler.get_jobs())
        except Exception:  # noqa: BLE001
            logger.exception("z4j rq-scheduler: get_jobs failed")
            return []
        out: list[Schedule] = []
        for job in jobs:
            try:
                out.append(self._to_schedule(job))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "z4j rq-scheduler: failed to map job %r",
                    getattr(job, "id", "?"),
                )
        return out

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        try:
            jobs = list(self.scheduler.get_jobs())
        except Exception:  # noqa: BLE001
            return None
        for job in jobs:
            if _safe_str(getattr(job, "id", "")) == schedule_id:
                try:
                    return self._to_schedule(job)
                except Exception:  # noqa: BLE001
                    return None
        return None

    # ------------------------------------------------------------------
    # SchedulerAdapter - write
    # ------------------------------------------------------------------

    async def create_schedule(self, spec: Schedule) -> Schedule:
        # Deferred to v1.1 - capabilities() omits "create" so the
        # dashboard hides the button. If the brain bypasses the gate
        # we fail loudly.
        raise NotImplementedError(
            "create_schedule is deferred to v1.1 for rq-scheduler; "
            "create schedules via your project's Python entrypoint "
            "(scheduler.cron(...) / scheduler.schedule(...)) for now.",
        )

    async def update_schedule(
        self, schedule_id: str, spec: Schedule,
    ) -> Schedule:  # noqa: ARG002
        raise NotImplementedError(
            "update_schedule is deferred to v1.1 for rq-scheduler.",
        )

    async def delete_schedule(self, schedule_id: str) -> CommandResult:
        try:
            jobs = list(self.scheduler.get_jobs())
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                status="failed", error=f"get_jobs failed: {exc}",
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
            self.scheduler.cancel(target)
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                status="failed", error=f"cancel failed: {exc}",
            )
        return CommandResult(
            status="success",
            result={"schedule_id": schedule_id},
        )

    async def enable_schedule(self, schedule_id: str) -> CommandResult:
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
        # Same disclaimer as enable - disable maps to delete in
        # rq-scheduler. We preserve the original call semantics and
        # let the brain decide whether to surface this as disabled
        # vs gone.
        return await self.delete_schedule(schedule_id)

    async def trigger_now(self, schedule_id: str) -> CommandResult:
        sched = await self.get_schedule(schedule_id)
        if sched is None:
            raise NotFoundError(f"schedule {schedule_id!r} not found")

        try:
            from datetime import timedelta

            # Pull the raw job so we can re-enqueue it immediately.
            jobs = list(self.scheduler.get_jobs())
            target = None
            for job in jobs:
                if _safe_str(getattr(job, "id", "")) == schedule_id:
                    target = job
                    break
            if target is None:
                return CommandResult(
                    status="failed",
                    error=f"schedule {schedule_id!r} vanished between lookup and trigger",
                )
            new_job = self.scheduler.enqueue_in(
                timedelta(0),
                target.func_name,
                *list(getattr(target, "args", [])),
                **dict(getattr(target, "kwargs", {})),
            )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                status="failed", error=f"trigger_now failed: {exc}",
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
        return set(DEFAULT_CAPABILITIES)

    # ------------------------------------------------------------------
    # Internal: Job → Schedule projection
    # ------------------------------------------------------------------

    def _to_schedule(self, job: Any) -> Schedule:
        now = datetime.now(UTC)
        sched_id = _safe_str(getattr(job, "id", "")) or str(uuid4())
        cron_expr = _safe_str(
            getattr(job, "meta", {}).get("cron_string", "") if isinstance(
                getattr(job, "meta", None), dict,
            ) else "",
        )
        if cron_expr:
            kind = ScheduleKind.CRON
            expression = cron_expr
        else:
            interval = getattr(job, "meta", {}).get("interval") if isinstance(
                getattr(job, "meta", None), dict,
            ) else None
            if interval:
                kind = ScheduleKind.INTERVAL
                expression = _safe_str(interval)
            else:
                # Fall back to clocked-at the job's scheduled_for.
                kind = ScheduleKind.CLOCKED
                scheduled_for = getattr(job, "scheduled_for", None)
                expression = (
                    scheduled_for.isoformat()
                    if isinstance(scheduled_for, datetime) else "unknown"
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


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


def _safe_uuid(value: str) -> UUID:
    """Deterministic UUID5 from a string id so the brain can dedupe."""
    try:
        return UUID(value)
    except Exception:  # noqa: BLE001
        import uuid as _uuid
        return _uuid.uuid5(_uuid.NAMESPACE_OID, value)


__all__ = ["RqSchedulerAdapter"]
