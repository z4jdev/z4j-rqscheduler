# z4j-rqscheduler

[![PyPI version](https://img.shields.io/pypi/v/z4j-rqscheduler.svg)](https://pypi.org/project/z4j-rqscheduler/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-rqscheduler.svg)](https://pypi.org/project/z4j-rqscheduler/)
[![License](https://img.shields.io/pypi/l/z4j-rqscheduler.svg)](https://github.com/z4jdev/z4j-rqscheduler/blob/main/LICENSE)


**License:** Apache 2.0
**Status:** v2026.5 - first public release alongside `z4j-rq`.

z4j scheduler-axis adapter for
[rq-scheduler](https://github.com/rq/rq-scheduler). Pairs with
`z4j-rq` the same way `z4j-celerybeat` pairs with `z4j-celery` -
adds scheduled-job management to the dashboard Schedules page for
projects that run RQ.

## Install

```bash
pip install z4j[rq,rqscheduler]
# or standalone:
pip install z4j-rqscheduler
```

## Capabilities

Advertised via the standard `SchedulerAdapter.capabilities()`
contract:

| Token | Status | Note |
|---|---|---|
| `list` | ✅ | Reads every scheduled job from the rq-scheduler Redis zset |
| `enable` / `disable` | ✅ | via `Scheduler.enqueue_in(0, ...)` pause/resume pattern |
| `trigger_now` | ✅ | `Scheduler.enqueue_in(timedelta(0), func, *args)` |
| `delete` | ✅ | `Scheduler.cancel(job)` |
| `create` / `update` | ⏸️ | Deferred to v1.1 - the UI surface for creating schedules lives on the Celery track first |

## See also

- [`packages/z4j-rq/`](../z4j-rq/) - the engine adapter this pairs with.
- [`docs/ADAPTER.md`](../../docs/ADAPTER.md) - generic adapter guide.

## License

Apache 2.0 - see [LICENSE](LICENSE). This package is deliberately permissively licensed so that proprietary Django / Flask / FastAPI applications can import it without any license concerns.

## Links

- Homepage: <https://z4j.com>
- Documentation: <https://z4j.dev>
- Source: <https://github.com/z4jdev/z4j-rqscheduler>
- Issues: <https://github.com/z4jdev/z4j-rqscheduler/issues>
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: `security@z4j.com` (see [SECURITY.md](SECURITY.md))
