"""Shared fixtures for z4j-rqscheduler unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest


@dataclass
class FakeJob:
    id: str
    func_name: str = "myapp.tasks.nightly"
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    origin: str = "default"
    scheduled_for: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class FakeScheduler:
    def __init__(self) -> None:
        self._jobs: list[FakeJob] = []
        self.cancelled: list[str] = []
        self.enqueued: list[dict[str, Any]] = []

    # --- SchedulerAdapter duck-type surface -----------------------

    def get_jobs(self) -> list[FakeJob]:
        return list(self._jobs)

    def cancel(self, job: FakeJob) -> None:
        self.cancelled.append(job.id)
        self._jobs = [j for j in self._jobs if j.id != job.id]

    def enqueue_in(self, td: timedelta, func_name: str, *args, **kwargs):
        new = FakeJob(id=f"new-{len(self.enqueued) + 1}", func_name=func_name,
                      args=tuple(args), kwargs=dict(kwargs))
        self.enqueued.append(
            {"td_s": td.total_seconds(), "func": func_name,
             "args": tuple(args), "kwargs": dict(kwargs)},
        )
        return new

    # --- test helpers ---------------------------------------------

    def register(self, job: FakeJob) -> None:
        self._jobs.append(job)


@pytest.fixture
def scheduler() -> FakeScheduler:
    s = FakeScheduler()
    s.register(
        FakeJob(
            id="job-cron-1",
            func_name="myapp.tasks.nightly",
            args=("payload",),
            meta={"cron_string": "0 3 * * *"},
        ),
    )
    s.register(
        FakeJob(
            id="job-interval-1",
            func_name="myapp.tasks.refresh",
            meta={"interval": 60},
        ),
    )
    return s
