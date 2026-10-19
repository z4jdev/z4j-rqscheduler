"""Real Redis transactions must arbitrate native RQ dispatch and operator writes."""

import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue
from rq.job import Job
from rq_scheduler import Scheduler
from z4j_core.schedule_external import external_schedule_configuration, normalize_external_schedule
from z4j_rqscheduler.native import NativeRqControl


def task(value=0):
    return value


@pytest.fixture
def native(tmp_path):
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip(
            "real Redis regression needs the redis-server executable; live launch lane is mandatory"
        )
    path = tmp_path / "redis.sock"
    process = subprocess.Popen(
        [binary, "--port", "0", "--unixsocket", str(path), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connection = Redis(unix_socket_path=str(path))
    try:
        for _ in range(100):
            if path.exists():
                break
            time.sleep(0.01)
        assert connection.ping()
        scheduler = Scheduler(connection=connection, queue_name="native-test")
        yield NativeRqControl(scheduler, uuid4(), managed=True, tasks={"record": task})
    finally:
        connection.close()
        process.terminate()
        process.wait(timeout=5)


def create(native):
    return native.mutate(
        operation="create",
        schedule_id=None,
        expected=None,
        definition={
            "name": "native",
            "task_name": "record",
            "kind": "interval",
            "expression": "60",
            "args": [7],
        },
    )


def normalized(row):
    return normalize_external_schedule(row.model_dump(mode="json"), owner="rq-scheduler")


def test_first_enqueue_does_not_invalidate_edit_token_and_pause_is_durable(native):
    row = create(native)
    native.connection.zadd(native.active, {row.external_id: datetime.now(UTC).timestamp() - 1})
    stale_job = Job.fetch(row.external_id, connection=native.connection)
    native.scheduler.enqueue_job(stale_job)
    after = native.snapshot()[0]
    assert external_schedule_configuration(normalized(after)) == external_schedule_configuration(
        normalized(row)
    )
    paused = native.mutate(
        operation="update",
        schedule_id=row.external_id,
        expected=normalized(row),
        definition={"is_enabled": False, "args": [99]},
    )
    restarted = NativeRqControl(
        Scheduler(connection=native.connection, queue_name="native-test"),
        native.project_id,
        managed=True,
        tasks={"record": task},
    )
    assert restarted.snapshot()[0].is_enabled is False
    assert restarted.snapshot()[0].args == [99]
    queue = Queue("native-test", connection=native.connection)
    before = queue.count
    restarted.scheduler.enqueue_job(stale_job)
    assert queue.count == before
    native.mutate(
        operation="delete", schedule_id=row.external_id, expected=normalized(paused), definition={}
    )
    assert restarted.snapshot() == []


def test_native_enqueue_aborts_when_configuration_changes_after_read(native, monkeypatch):
    row = create(native)
    native.connection.zadd(native.active, {row.external_id: datetime.now(UTC).timestamp() - 1})
    job = Job.fetch(row.external_id, connection=native.connection)
    method = "_enqueue_job" if hasattr(Queue, "_enqueue_job") else "enqueue_job"
    original = getattr(Queue, method)

    def racing_enqueue(queue, candidate, *args, **kwargs):
        native.connection.hset(job.key, "description", "concurrent native edit")
        return original(queue, candidate, *args, **kwargs)

    monkeypatch.setattr(Queue, method, racing_enqueue)
    native.scheduler.enqueue_job(job)
    assert Queue("native-test", connection=native.connection).count == 0
    assert native.connection.zscore(native.active, row.external_id) is not None


def test_one_second_interval_remains_due_on_every_native_poll(native, monkeypatch):
    """Fractional creation/dispatch times must not add an extra native tick."""
    import rq_scheduler.scheduler as upstream
    import z4j_rqscheduler.native as control

    instant = datetime(2026, 10, 15, 12, 0, 0, 750000, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return instant.replace(tzinfo=None)

    monkeypatch.setattr(control, "datetime", Clock)
    monkeypatch.setattr(upstream, "datetime", Clock)
    row = native.mutate(
        operation="create",
        schedule_id=None,
        expected=None,
        definition={
            "name": "one-second",
            "task_name": "record",
            "kind": "interval",
            "expression": "1",
        },
    )
    queue = Queue("native-test", connection=native.connection)
    assert list(native.scheduler.get_jobs_to_queue()) == []
    for tick in range(1, 5):
        instant += timedelta(seconds=1)
        due = list(native.scheduler.get_jobs_to_queue())
        assert [job.id for job in due] == [row.external_id]
        native.scheduler.enqueue_job(due[0])
        assert queue.count == tick
        assert list(native.scheduler.get_jobs_to_queue()) == []
