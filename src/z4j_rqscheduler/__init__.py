"""z4j-rqscheduler - rq-scheduler adapter for z4j's Schedules UI.

Public API:

- :class:`RqSchedulerAdapter` - pass to ``install_agent(schedulers=[...])``.

Licensed under Apache License 2.0.
"""

from __future__ import annotations

from z4j_rqscheduler.scheduler import RqSchedulerAdapter

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("z4j-rqscheduler")
except PackageNotFoundError:  # source checkout, no installed metadata
    from z4j_core.version import __version__  # type: ignore[no-redef]

__all__ = ["RqSchedulerAdapter", "__version__"]
