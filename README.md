# z4j-rqscheduler

[![PyPI version](https://img.shields.io/pypi/v/z4j-rqscheduler.svg)](https://pypi.org/project/z4j-rqscheduler/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-rqscheduler.svg)](https://pypi.org/project/z4j-rqscheduler/)
[![License](https://img.shields.io/pypi/l/z4j-rqscheduler.svg)](https://github.com/z4jdev/z4j-rqscheduler/blob/main/LICENSE)

The rq-scheduler adapter for [z4j](https://z4j.com).

Surfaces rq-scheduler periodic / interval / cron jobs on the
dashboard's Schedules page, read, trigger, disable (cancel), delete.

## Compatibility

- rq-scheduler 0.11+ (no upper cap)
- Python 3.11+

Full per-adapter matrix at <https://z4j.dev/reference/compatibility/>.

## What it ships

| Capability | Notes |
|---|---|
| List schedules | every job rq-scheduler currently tracks |
| Disable | cancels the scheduled job (rq-scheduler has no enabled flag); re-enable by re-registering it from your code |
| Trigger now | enqueues the task immediately, outside the schedule |
| Delete | clean removal from the rq-scheduler set |
| Boot inventory | full snapshot at agent connect; existing schedules show up without editing |

Schedule **create** and **update** are not yet supported from the
dashboard for rq-scheduler, so the brain greys those actions out.
Define schedules in your project's Python entrypoint
(`scheduler.cron(...)` / `scheduler.schedule(...)`); z4j inventories them and
exposes the supported trigger, disable, and delete controls once they exist.
Enable is not advertised because a cancelled definition cannot be restored by
rq-scheduler; re-register it from application code instead.

## Install

```bash
pip install z4j-rq z4j-rqscheduler
```

```python
import os

from rq import Queue
from rq_scheduler import Scheduler
from redis import Redis
from z4j_bare import install_agent
from z4j_rq import RqEngineAdapter
from z4j_rqscheduler import RqSchedulerAdapter

redis = Redis(host="localhost")
queue = Queue(connection=redis)
scheduler = Scheduler(queue=queue, connection=redis)

install_agent(
    engines=[RqEngineAdapter(rq_app=queue)],
    schedulers=[RqSchedulerAdapter(scheduler=scheduler)],
    brain_url="https://brain.example.com",
    token="z4j_agent_...",
    project_id="my-project",
    hmac_secret=os.environ["Z4J_HMAC_SECRET"],
)
```

## Pairs with

- [`z4j-rq`](https://github.com/z4jdev/z4j-rq), engine adapter

## Reliability

- rq-scheduler has no registry-change signal, so z4j refreshes inventory at
  boot and during periodic reconciliation.
- Redis calls run on the adapter's bounded offload pool. A mutation that
  exceeds its 10-second timeout is reported as indeterminate because the
  worker thread may still complete it.

## Documentation

Full docs at [z4j.dev/schedulers/rq-scheduler/](https://z4j.dev/schedulers/rq-scheduler/).

## License

Apache-2.0, see [LICENSE](LICENSE).

## Links

- Homepage: https://z4j.com
- Documentation: https://z4j.dev
- PyPI: https://pypi.org/project/z4j-rqscheduler/
- Issues: https://github.com/z4jdev/z4j-rqscheduler/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: security@z4j.com (see [SECURITY.md](SECURITY.md))
