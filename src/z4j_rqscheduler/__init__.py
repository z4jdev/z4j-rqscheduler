"""z4j-rqscheduler - rq-scheduler adapter for z4j's Schedules UI.

Public API:

- :class:`RqSchedulerAdapter` - pass to ``install_agent(schedulers=[...])``.

Licensed under Apache License 2.0.
"""

from __future__ import annotations

from z4j_rqscheduler.scheduler import RqSchedulerAdapter

__version__ = "1.3.0"

__all__ = ["RqSchedulerAdapter", "__version__"]
