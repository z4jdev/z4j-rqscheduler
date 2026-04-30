# z4j-rqscheduler

[![PyPI version](https://img.shields.io/pypi/v/z4j-rqscheduler.svg)](https://pypi.org/project/z4j-rqscheduler/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-rqscheduler.svg)](https://pypi.org/project/z4j-rqscheduler/)
[![License](https://img.shields.io/pypi/l/z4j-rqscheduler.svg)](https://github.com/z4jdev/z4j-rqscheduler/blob/main/LICENSE)

The rq-scheduler adapter for [z4j](https://z4j.com).

Surfaces rq-scheduler periodic / interval / cron jobs on the
dashboard's Schedules page, read, create, update, enable, disable,
trigger, delete.

## What it ships

| Capability | Notes |
|---|---|
| List schedules | every job rq-scheduler currently tracks |
| Create schedule | interval / cron / one-shot |
| Update | schedule expression, args, kwargs, queue |
| Enable / disable | via re-add / cancel |
| Trigger now | enqueues the task immediately, outside the schedule |
| Delete | clean removal from the rq-scheduler set |
| Boot inventory | full snapshot at agent connect; existing schedules show up without editing |

## Install

```bash
pip install z4j-rq z4j-rqscheduler
```

```python
from rq import Queue
from rq_scheduler import Scheduler
from redis import Redis
from z4j_bare import install_agent
from z4j_rq import RQEngineAdapter
from z4j_rqscheduler import RQSchedulerAdapter

redis = Redis(host="localhost")
queue = Queue(connection=redis)
scheduler = Scheduler(queue=queue, connection=redis)

install_agent(
    engines=[RQEngineAdapter(queues=[queue])],
    schedulers=[RQSchedulerAdapter(scheduler=scheduler)],
    brain_url="https://brain.example.com",
    token="z4j_agent_...",
    project_id="my-project",
)
```

## Pairs with

- [`z4j-rq`](https://github.com/z4jdev/z4j-rq), engine adapter

## Reliability

- No exception from the adapter ever propagates back into rq-scheduler
  or your job code.
- Schedule writes to Redis are atomic; if the brain is unreachable,
  the local Redis write is never affected.

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
