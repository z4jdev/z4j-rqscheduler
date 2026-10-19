"""Redis-backed native RQ schedules with a cooperating scheduler producer."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from z4j_core.models import Schedule
from z4j_core.schedule_external import (
    canonical_external_json,
    external_schedule_configuration,
    normalize_external_schedule,
)


class NativeRqControl:
    """Serialize managed RQ producer and operator changes with Redis transactions.

    Every rq-scheduler producer for this registry must install managed control.
    Pause state lives in Redis alongside the native job, never in process RAM.
    A producer watches and rereads the job, atomically rejecting stale work.
    """

    def __init__(
        self, scheduler: Any, project_id: UUID, *, managed: bool, tasks: dict[str, Any]
    ) -> None:
        self.scheduler = scheduler
        self.project_id = project_id
        self.managed = managed
        self.tasks = tasks
        self.connection = scheduler.connection
        self.active = scheduler.scheduled_jobs_key
        self.paused = self.active + ":z4j-paused"
        if managed:
            if getattr(scheduler, "_z4j_native_control", None) is not None:
                raise ValueError("RQ scheduler already has managed native control")
            scheduler._z4j_native_control = self
            scheduler.enqueue_job = self._enqueue_due

    def _read(self, connection: Any) -> list[Schedule]:
        from rq.exceptions import NoSuchJobError

        active = dict(connection.zrange(self.active, 0, -1, withscores=True))
        paused = (
            dict(connection.zrange(self.paused, 0, -1, withscores=True)) if self.managed else {}
        )
        if set(active) & set(paused):
            raise ValueError("RQ schedule is present in both active and paused registries")
        rows = []
        for raw_id, score in {**active, **paused}.items():
            key = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            try:
                job = self.scheduler.job_class.fetch(key, connection=self.connection)
            except NoSuchJobError as exc:
                raise ValueError(
                    "RQ registry contains a missing job; retry after native cleanup"
                ) from exc
            rows.append(self._to_schedule(job, score, raw_id not in paused))
        return rows

    def snapshot(self) -> list[Schedule]:
        """Read a complete optimistic snapshot, refusing concurrent changes."""
        from redis.exceptions import WatchError

        for _ in range(3):
            try:
                with self.connection.pipeline() as pipe:
                    pipe.watch(self.active, self.paused)
                    ids = set(pipe.zrange(self.active, 0, -1)) | (
                        set(pipe.zrange(self.paused, 0, -1)) if self.managed else set()
                    )
                    if ids:
                        pipe.watch(
                            *(
                                self.scheduler.job_class.key_for(
                                    i.decode() if isinstance(i, bytes) else i
                                )
                                for i in ids
                            )
                        )
                    rows = self._read(pipe)
                    pipe.multi()
                    pipe.ping()
                    pipe.execute()
                    return rows
            except WatchError:
                continue
        raise ValueError("RQ schedule registry changed during inventory; retrying next snapshot")

    def _to_schedule(self, job: Any, score: float, enabled: bool) -> Schedule:
        now = datetime.now(UTC)
        meta = job.meta
        cron = meta.get("cron_string")
        interval = meta.get("interval")
        kind = "cron" if cron else "interval" if interval else "clocked"
        expression = str(cron or interval or datetime.fromtimestamp(score, UTC).isoformat())
        name = meta.get("_z4j_schedule_name") or f"{job.func_name[:90]} [{job.id[:90]}]"
        config = {
            k: v
            for k, v in meta.items()
            if k not in {"repeat", "_z4j_schedule_name", "_z4j_task_name"}
        }
        config.update(
            native_queue=job.origin,
            timeout=job.timeout
            if job.timeout is not None
            else self.scheduler.get_queue_for_job(job)._default_timeout,
            result_ttl=job.result_ttl,
            ttl=job.ttl,
            enqueue_at_front=getattr(job, "enqueue_at_front", False),
        )
        return Schedule.model_validate_json(
            canonical_external_json(
                {
                    "id": str(uuid5(NAMESPACE_URL, "rq-scheduler:" + job.id)),
                    "project_id": str(self.project_id),
                    "engine": "rq",
                    "scheduler": "rq-scheduler",
                    "name": name,
                    "task_name": meta.get("_z4j_task_name") or job.func_name,
                    "kind": kind,
                    "expression": expression,
                    "timezone": "UTC",
                    "queue": meta.get("_z4j_queue", job.origin),
                    "args": list(job.args),
                    "kwargs": job.kwargs,
                    "is_enabled": enabled,
                    "external_id": job.id,
                    "next_run_at": datetime.fromtimestamp(score, UTC).isoformat()
                    if enabled
                    else None,
                    "source": "rq-scheduler:redis",
                    "source_hash": hashlib.sha256(canonical_external_json(config)).hexdigest(),
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
        )

    @staticmethod
    def _enqueue_transaction(queue: Any, job: Any, pipe: Any, *, at_front: bool = False) -> Any:
        """Enter MULTI explicitly across RQ 1 and 2, after watching dependencies.

        RQ 1's enqueue_job writes directly; RQ 2 moved dependency setup into
        that method. Use each version's enqueue primitive after shared setup,
        so no mutation can escape WATCH before the caller commits the batch.
        """
        if not queue.is_async:
            raise ValueError("managed RQ scheduling requires an asynchronous queue")
        job = queue.setup_dependencies(job, pipeline=pipe)
        if not pipe.explicit_transaction:
            pipe.multi()
        pipe.sadd(queue.redis_queues_keys, queue.key)
        if job.get_status(refresh=False) == "deferred":
            return job
        enqueue = getattr(queue, "_enqueue_job", queue.enqueue_job)
        return enqueue(job, pipeline=pipe, at_front=at_front)

    def _enqueue_due(self, stale_job: Any) -> None:
        from redis.exceptions import WatchError
        from rq_scheduler.utils import get_next_scheduled_time, to_unix

        try:
            with self.connection.pipeline() as pipe:
                pipe.watch(self.active, self.paused, stale_job.key)
                score = pipe.zscore(self.active, stale_job.id)
                if score is None or score > datetime.now(UTC).timestamp():
                    return
                if pipe.zscore(self.paused, stale_job.id) is not None:
                    return
                job = self.scheduler.job_class.fetch(stale_job.id, connection=self.connection)
                queue = self.scheduler.get_queue_for_job(job)
                if not queue.is_async:
                    raise ValueError("managed RQ scheduling requires an asynchronous queue")
                repeat = job.meta.get("repeat")
                if repeat:
                    job.meta["repeat"] = int(repeat) - 1
                self._enqueue_transaction(
                    queue, job, pipe, at_front=bool(getattr(job, "enqueue_at_front", False))
                )
                pipe.zrem(self.active, job.id)
                if repeat is None or job.meta.get("repeat") != 0:
                    if job.meta.get("interval"):
                        pipe.zadd(
                            self.active,
                            {job.id: to_unix(datetime.now(UTC)) + int(job.meta["interval"])},
                        )
                    elif job.meta.get("cron_string"):
                        stamp = get_next_scheduled_time(
                            job.meta["cron_string"],
                            use_local_timezone=job.meta.get("use_local_timezone", False),
                        )
                        pipe.zadd(self.active, {job.id: stamp.replace(tzinfo=UTC).timestamp()})
                pipe.execute()
        except WatchError:
            # A pause/edit won the race. No enqueue was committed; the next
            # native tick reads the updated registry, without replaying this job.
            return

    def trigger(self, schedule_id: str) -> str:
        with self.connection.pipeline() as pipe:
            pipe.watch(self.active, self.paused, self.scheduler.job_class.key_for(schedule_id))
            rows = self._read(pipe)
            if not any(s.external_id == schedule_id for s in rows):
                raise ValueError("native schedule not found")
            job = self.scheduler.job_class.fetch(schedule_id, connection=self.connection)
            # Enqueue directly, preserving routing and arguments without adding
            # a second scheduled definition or changing the repeating job.
            queue = self.scheduler.get_queue_for_job(job)
            sent = queue.create_job(
                func=job.func_name,
                args=job.args,
                kwargs=job.kwargs,
                timeout=job.timeout,
                result_ttl=job.result_ttl,
                ttl=job.ttl,
            )
            self._enqueue_transaction(queue, sent, pipe)
            pipe.execute()
            return str(sent.id)

    def mutate(  # noqa: PLR0912, PLR0915 - the watched read and native write share one transaction
        self,
        *,
        operation: str,
        schedule_id: str | None,
        expected: dict[str, Any] | None,
        definition: dict[str, Any],
    ) -> Schedule | None:
        from rq.job import Job
        from rq_scheduler.utils import get_next_scheduled_time, to_unix

        if not self.managed:
            raise ValueError(
                "dashboard mutations require managed=True on every RQ scheduler producer"
            )
        if set(definition) - {
            "name",
            "task_name",
            "kind",
            "expression",
            "timezone",
            "queue",
            "args",
            "kwargs",
            "is_enabled",
        }:
            raise ValueError("unsupported native schedule fields")
        with self.connection.pipeline() as pipe:
            pipe.watch(self.active, self.paused)
            if schedule_id is not None:
                pipe.watch(Job.key_for(schedule_id))
            rows = self._read(pipe)
            current = next((s for s in rows if s.external_id == schedule_id), None)
            job = None
            if operation != "create":
                if (
                    current is None
                    or expected is None
                    or external_schedule_configuration(
                        normalize_external_schedule(
                            current.model_dump(mode="json"), owner="rq-scheduler"
                        )
                    )
                    != external_schedule_configuration(expected)
                ):
                    raise ValueError("native schedule changed; refresh before editing")
                job = self.scheduler.job_class.fetch(schedule_id, connection=self.connection)
                if operation == "delete":
                    if definition:
                        raise ValueError("delete cannot change a definition")
                    pipe.multi()
                    pipe.zrem(self.active, schedule_id)
                    pipe.zrem(self.paused, schedule_id)
                    pipe.execute()
                    return None
            elif schedule_id is not None or expected is not None:
                raise ValueError("create cannot replace an existing schedule")
            if operation not in {"create", "update"}:
                raise ValueError("unsupported native operation")
            now = datetime.now(UTC)
            data = (
                current.model_dump(mode="json")
                if current
                else {
                    "id": str(uuid4()),
                    "project_id": str(self.project_id),
                    "engine": "rq",
                    "scheduler": "rq-scheduler",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
            data.update(definition)
            candidate = Schedule.model_validate_json(canonical_external_json(data))
            if candidate.timezone != "UTC":
                raise ValueError("managed RQ schedules require UTC")
            if any(s.name == candidate.name and s.external_id != schedule_id for s in rows):
                raise ValueError("native schedule name already exists")
            task = self.tasks.get(candidate.task_name)
            if job is not None and current is not None and candidate.task_name == current.task_name:
                task = job.func_name
            if task is None:
                task = next(
                    (
                        self.scheduler.job_class.fetch(
                            s.external_id, connection=self.connection
                        ).func_name
                        for s in rows
                        if s.task_name == candidate.task_name
                    ),
                    None,
                )
            if task is None:
                raise ValueError(
                    "task must exist in a native schedule or be passed in tasks={name: callable}"
                )
            meta = dict(job.meta) if job is not None else {}
            meta.pop("cron_string", None)
            meta.pop("interval", None)
            meta.update(
                _z4j_schedule_name=candidate.name,
                _z4j_task_name=candidate.task_name,
                _z4j_queue=candidate.queue,
            )
            if candidate.kind == "cron":
                score = (
                    get_next_scheduled_time(candidate.expression, use_local_timezone=False)
                    .replace(tzinfo=UTC)
                    .timestamp()
                )
                meta.update(cron_string=candidate.expression, use_local_timezone=False)
            elif candidate.kind == "interval":
                interval = int(candidate.expression)
                if interval <= 0 or str(interval) != candidate.expression:
                    raise ValueError("RQ interval must be positive integer seconds")
                # Native due-job polling truncates UTC timestamps to seconds.
                # Fractional scores would postpone every interval by one tick.
                score = to_unix(now) + interval
                meta["interval"] = interval
            elif candidate.kind == "clocked":
                stamp = datetime.fromisoformat(candidate.expression)
                if stamp.tzinfo is None:
                    raise ValueError("clocked schedules require an offset")
                score = stamp.timestamp()
            else:
                raise ValueError("RQ supports cron, interval and clocked schedules")
            if current is not None and all(
                getattr(current, k) == getattr(candidate, k)
                for k in ("kind", "expression", "timezone")
            ):
                previous_score = pipe.zscore(self.active, schedule_id)
                if previous_score is None:
                    previous_score = pipe.zscore(self.paused, schedule_id)
                if previous_score is not None and (
                    not candidate.is_enabled or previous_score > now.timestamp()
                ):
                    score = previous_score
            new_job = self.scheduler.job_class.create(
                task,
                args=candidate.args,
                kwargs=candidate.kwargs,
                connection=self.connection,
                id=schedule_id or str(uuid4()),
                origin=candidate.queue or self.scheduler.queue_name,
                meta=meta,
                timeout=job.timeout if job is not None else None,
                result_ttl=job.result_ttl if job is not None else -1,
            )
            # Preserve native execution options when updating a job definition.
            if job is not None:
                job.func_name = new_job.func_name
                job.instance = new_job.instance
                job.args = new_job.args
                job.kwargs = new_job.kwargs
                job.origin = new_job.origin
                job.meta = meta
                new_job = job
            projected = self._to_schedule(new_job, score, candidate.is_enabled)
            pipe.multi()
            new_job.save(pipeline=pipe)
            pipe.persist(new_job.key)
            pipe.zrem(self.active, new_job.id)
            pipe.zrem(self.paused, new_job.id)
            pipe.zadd(self.active if candidate.is_enabled else self.paused, {new_job.id: score})
            pipe.execute()
            return projected
