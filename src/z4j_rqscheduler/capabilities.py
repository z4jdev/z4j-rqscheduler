"""Capability tokens advertised by :class:`RqSchedulerAdapter`.

Honest about rq-scheduler's engine surface:

- ✅ ``list`` - full Redis zset walk
- ✅ ``enable`` / ``disable`` / ``trigger_now`` / ``delete``
- ⏸️ ``create`` / ``update`` deferred to v1.1 (schedule-creation
  UI lives on the Celery track first, and rq-scheduler's
  create surface is a Python decorator pattern rather than an API
  shape the dashboard can invoke cleanly).
"""

from __future__ import annotations

DEFAULT_CAPABILITIES: frozenset[str] = frozenset(
    {
        "list",
        "enable",
        "disable",
        "trigger_now",
        "delete",
    },
)


__all__ = ["DEFAULT_CAPABILITIES"]
