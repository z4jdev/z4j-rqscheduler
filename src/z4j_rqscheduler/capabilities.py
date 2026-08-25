"""Capability tokens advertised by :class:`RqSchedulerAdapter`.

rq-scheduler's engine surface:

- ✅ ``list`` - full Redis zset walk
- ``trigger_now`` and ``delete`` are supported.
- ``disable`` is destructive: it cancels and removes the scheduled job.
- ``enable`` is not advertised because rq-scheduler cannot restore a job
  removed by ``disable``.
- ``create`` and ``update`` are not supported.
"""

from __future__ import annotations

DEFAULT_CAPABILITIES: frozenset[str] = frozenset(
    {
        "list",
        "disable",
        "trigger_now",
        "delete",
    },
)


__all__ = ["DEFAULT_CAPABILITIES"]
