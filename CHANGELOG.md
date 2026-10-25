# Changelog

All notable changes to `inverge-nexus` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[semantic versioning](https://semver.org/).

## [1.3.0] — 2026-10-25

### Added

- **Nexus Telescope** — `nexus.telescope` records request journeys: Django
  (`DjangoTelescopeMiddleware`: ORM queries, model saves/deletes), FastAPI /
  Starlette and any ASGI app (`TelescopeASGIMiddleware`), Flask
  (`instrument_flask`) and any WSGI app, Celery tasks as jobs that join the
  dispatching trace, plus `requests` / `httpx` (W3C `traceparent` propagated),
  SQLAlchemy, redis-py and the `logging` module. Spans (`with telescope.span()`,
  `@telescope.traced()`), jobs/commands/schedules, tail sampling and console
  settings. Context on `contextvars` (per thread and per asyncio task); delivery
  on a fork-safe background thread whose `flush()` waits for in-flight batches.
  Switch off with `NEXUS_TELESCOPE_ENABLED=false`.

## [1.2.0] — 2026-10-21

### Added

- **Switch services off without removing code** — `NEXUS_<SERVICE>_ENABLED=false`
  (and `NEXUS_ENABLED=false` for all) or `services={...}` / `enabled=False` in
  code; off wins. A switched-off service makes no request and never raises (even
  without `silent`), events/logs aren't buffered, the realtime socket doesn't
  connect. `client.enabled(service)`.

## [1.1.0] — 2026-10-21

### Added

- **Live Activity templates.** `client.live_activities.template(key, activity_id)`
  starts an activity from a template designed in the Nexus console (or an inline
  design) and sends only data; the mobile SDKs render it on iOS + Android in each
  device's language. New builder setters `step`, `eta_at`, `eta`, `value`/`values`,
  `canceled`, `done`, `data`, plus `lang` / `alert` for the start. Updates and ends
  send partial `data` the server merges. Plain forms `start_template`,
  `update_data`, `end(..., data=...)` — sync and async.

### Unchanged

- The plain `activity(type, id)` contentState path; `progress()` / texts on their
  own still send `contentState`.

## [1.0.0] — 2026-10-04

First release. Full coverage of the Nexus partner API from Python.

### Added

- **Clients** — `NexusClient` (sync) and `nexus.aio.AsyncNexusClient` (asyncio),
  sharing one definition of every endpoint, so the two can't drift apart.
- **Products** — sessions, events, logs, errors, feature flags, Remote Config,
  realtime rooms, deep links, surveys, push, in-app messages, Live Activities,
  session replay, and Voice (sessions/legs/IVR/transfer/conference/presence/
  quality/recordings).
- **Module-level default client** — `nexus.init()`, `nexus.track()`,
  `nexus.identify()`, `nexus.events.…`, for scripts and workers.
- **Ambient context** — `nexus.context` on `contextvars`, correct per thread and
  per asyncio task; identity flows into every capture without being passed down.
- **Buffered ingest** — events and logs batch on a background thread, grouped by
  identity, with a bounded queue, interval/size triggers, `atexit` flush and
  post-`fork()` recovery for prefork servers.
- **Integrations** — Django (app + middleware + settings + `manage.py
  nexus_test`), Flask (extension), FastAPI/Starlette (middleware, dependency,
  lifespan), Celery (signals + a queued dispatcher), plus generic WSGI and ASGI
  middleware for every other framework.
- **`NexusLogHandler`** — forwards `logging` records to Nexus logs and sends
  records with `exc_info` to error monitoring too. Safe on the root logger: it
  drops the SDK's own diagnostics *and* anything logged during a Nexus delivery,
  so `httpx`'s per-request INFO line can't feed the handler its own traffic.
- **`RealtimeSocket`** — listen to realtime rooms via `python-socketio`
  (optional extra).
- **Config layering** — explicit arguments sit on top of the `NEXUS_*`
  environment, so `NEXUS_BASE_URL` repoints a deploy whose key lives in code.
- **Graceful flags** — `is_enabled` / `variant` / `payload` log and fall back to
  the caller's default when evaluation fails; `evaluate()` still raises.
- **Transports** — stdlib `urllib` by default (zero dependencies), with
  `requests` / `httpx` picked up automatically when installed; `httpx` /
  `aiohttp` / thread-pool for async. All swappable.
- **Typed errors** — `ApiError` and friends carrying status, code and details;
  `silent=True` for telemetry that can never raise.
- **Retries** — exponential backoff with jitter and `Retry-After`, applied only
  to requests that are safe to repeat.
- **Fluent builders** — `push.notification()` and `live_activities.activity()`,
  shared between the sync and async clients.
- Typed throughout (`py.typed`); Python 3.8+.
