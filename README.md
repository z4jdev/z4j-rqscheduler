# z4j-rqscheduler

The native RQ Scheduler adapter for [z4j](https://z4j.com), with dashboard management
of native schedules. Dashboard management needs the z4j server, `z4j-core`, `z4j-bare`
and this adapter from the same release, plus the producer setup below.

## Compatibility

rq-scheduler 0.11+ and the RQ version required by z4j-rq; Python 3.11+.

## Installation and producer setup

Install `z4j-rqscheduler` and `z4j-bare` from the same release wave, plus the native
scheduler and broker/store dependencies used by your application. The example
assumes your task code, writable application-state directory and agent credentials
already exist. Use the project UUID from agent registration.

```python
import os
from redis import Redis
from rq_scheduler import Scheduler
from z4j_bare import install_agent
from z4j_rqscheduler import RqSchedulerAdapter
from myapp.jobs import cleanup

scheduler = Scheduler(connection=Redis.from_url(os.environ["REDIS_URL"]), queue_name="default")
adapter = RqSchedulerAdapter(
    scheduler=scheduler, managed=True, tasks={"myapp.cleanup": cleanup},
)
agent = install_agent(
    engines=[], schedulers=[adapter],
    brain_url=os.environ["Z4J_BRAIN_URL"], token=os.environ["Z4J_TOKEN"],
    hmac_secret=os.environ["Z4J_HMAC_SECRET"], project_id=os.environ["Z4J_PROJECT_ID"],
)
try:
    scheduler.run()
finally:
    agent.stop()
```

Every producer sharing this Redis schedule registry must install `managed=True`.
The ordinary uninstrumented rq-scheduler CLI must not run alongside it. The
managed producer uses Redis transactions to arbitrate dispatch against edits
and pauses. Redis retains definitions and a separate paused registry, so a
producer restart preserves both. Configure Redis persistence for Redis restarts.
An adapter with the default `managed=False` provides inventory and manual runs;
it does not advertise dashboard writes.

## Dashboard controls

The connected producer supports inventory, create, edit, pause, resume, delete
and **Run now**. A write is complete only after z4j accepts a snapshot confirming
the native source. The dashboard uses the current connection's advertised
capabilities; an unavailable producer cannot accept new edits. Repeating an
operation ID does not submit it twice. A timeout or uncertain outcome requires
inspection before submitting a new request.

A pause prevents future scheduling. Work already queued or running can finish.
Run now submits an additional execution without resuming a paused schedule.
Native scheduling keeps the upstream scheduler's timing and delivery semantics;
it does not acquire z4j-scheduler's catch-up, overlap or exactly-once guarantees.
Queue task history requires the corresponding engine adapter in the workers.

## Supported definitions and limits

- Create/edit supports native cron, positive integer interval seconds and offset-aware one-shot datetimes, in UTC.
- Queue selection is supported; an RQ worker must consume the selected queue.
- `tasks={name: callable}` registers new task choices. Existing jobs also supply their registered function reference; arbitrary dashboard imports are not accepted.
- Pause preserves the scheduled definition. Resume reactivates it. Delete removes scheduling without deleting execution history.
- Run now enqueues immediately without adding a second schedule or resetting the repeat cadence.

## Links

- [Documentation](https://docs.z4j.com/schedulers/rq-scheduler/)
- [Changelog](https://github.com/z4jdev/z4j-rqscheduler/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/z4jdev/z4j-rqscheduler/blob/main/SECURITY.md); report vulnerabilities to security@z4j.com
- [Apache-2.0 license](https://github.com/z4jdev/z4j-rqscheduler/blob/main/LICENSE)
