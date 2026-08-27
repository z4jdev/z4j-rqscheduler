# Changelog

## 1.9.1 (2026-08-26)

* Carried with the coordinated fleet release. No adapter behaviour changed.

## 1.9.0 (2026-08-25)

* Authoritative inventory failures now abort the snapshot instead of returning
  an empty or partial list that could false-delete live schedules in the brain.
* `enable` is no longer advertised. rq-scheduler's disable operation cancels
  and removes a job, and the adapter cannot restore that definition.

## 1.8.0 (2026-07-23)

* `trigger_now` and `get_schedule` offload their Redis I/O off the agent loop (the pre-offload synchronous scan is gone) so a Redis incident can no longer freeze it; a timed-out mutation is reported indeterminate.
* Part of the coordinated 1.8.0 fleet release (unified fleet version, green lint/format/import-boundary gate).

## 1.7.0 (2026-07-07)

* README corrected to `RqSchedulerAdapter` and `RqEngineAdapter(rq_app=...)`.
* Python 3.11 is now the minimum supported version (3.10 dropped).
* Part of the coordinated 1.7.0 fleet release (unified fleet version, green lint/format/import-boundary gate).

## 1.4.0 (2026-05-02)

Initial 1.4.0 release: rq-scheduler companion. Surfaces rq-scheduler schedules in the dashboard.
