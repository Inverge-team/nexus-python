# Nexus Python SDK — Full Reference

`inverge-nexus` is the official Python client for the **Inverge Nexus**
platform: product analytics, structured logging, error monitoring, sessions,
feature flags, Remote Config, realtime rooms, deep-link attribution, surveys,
push notifications, in-app messages, Live Activities, session replay, and
Voice (CPaaS) — all correlated to a single user journey.

- PyPI: `inverge-nexus` · import name `nexus`
- Python **3.8+** · **zero required dependencies** (stdlib `urllib` transport)
- Sync **and** asyncio clients, sharing one definition of the API
- First-class **Django**, **Flask**, **FastAPI/Starlette**, **Celery**
  integrations, plus generic **WSGI** and **ASGI** middleware for everything else

---

## Table of contents

1. [Installation](#1-installation)
2. [Quick start](#2-quick-start)
3. [Configuration](#3-configuration)
4. [Three ways to use it](#4-three-ways-to-use-it)
5. [Framework guides](#5-framework-guides)
   · [Django](#django) · [Flask](#flask) · [FastAPI / Starlette](#fastapi--starlette)
   · [Celery](#celery) · [Any WSGI app](#any-wsgi-app) · [Any ASGI app](#any-asgi-app)
   · [Plain scripts & workers](#plain-scripts--workers)
6. [Identity & context](#6-identity--context)
7. [Sessions](#7-sessions)
8. [Events](#8-events)
9. [Logs](#9-logs)
10. [Errors](#10-errors)
11. [Feature flags](#11-feature-flags)
12. [Remote Config](#12-remote-config)
13. [Realtime](#13-realtime)
14. [Deep links & attribution](#14-deep-links--attribution)
15. [Surveys](#15-surveys)
16. [Push notifications](#16-push-notifications)
17. [In-app messages](#17-in-app-messages)
18. [Live Activities](#18-live-activities)
19. [Session replay](#19-session-replay)
20. [Voice (CPaaS)](#20-voice-cpaas)
21. [Async client](#21-async-client)
22. [Batching, flushing & shutdown](#22-batching-flushing--shutdown)
23. [Errors & retries](#23-errors--retries)
24. [Transports & dispatchers](#24-transports--dispatchers)
25. [Testing](#25-testing)
26. [Low-level requests](#26-low-level-requests)

---

## 1. Installation

```bash
pip install inverge-nexus
```

Optional extras:

```bash
pip install "inverge-nexus[requests]"   # pooled HTTP (recommended for servers)
pip install "inverge-nexus[httpx]"      # httpx, incl. the async transport
pip install "inverge-nexus[realtime]"   # listen to realtime rooms (python-socketio)
pip install "inverge-nexus[all]"
```

Nothing is required: without extras the SDK uses `urllib` from the standard
library. If `requests` or `httpx` happens to be installed, the SDK picks it up
automatically for connection pooling.

---

## 2. Quick start

```python
import nexus

nexus.init("nxs_live_xxx")                       # or set NEXUS_API_KEY

nexus.identify("user_123", email="a@b.com", traits={"plan": "pro"})
nexus.track("order_placed", {"total": 42, "currency": "USD"})
nexus.get_client().realtime.emit("orders:42", "status", {"state": "shipped"})

nexus.flush()                                    # before a short script exits
```

Prefer an explicit object? Everything above works on a client too:

```python
from nexus import NexusClient

client = NexusClient("nxs_live_xxx")
client.events.track("order_placed", {"total": 42})
```

Services: `sessions`, `events`, `logs`, `errors`, `flags`, `remote_config`,
`links`, `realtime`, `surveys`, `push`, `inapp`, `live_activities`, `replay`,
`voice`.

---

## 3. Configuration

```python
from nexus import NexusClient, NexusConfig

client = NexusClient(
    "nxs_live_xxx",
    base_url="https://services.inverge.net",
    app_version="2.1.0",
    release=os.environ.get("GIT_SHA"),
    batch=True,
    silent=False,
)

# or build the config separately
config = NexusConfig(api_key="nxs_live_xxx", timeout=5.0)
client = NexusClient(config=config)

# or entirely from the environment
config = NexusConfig.from_env()
```

| Option | Type | Default | Purpose |
|---|---|---|---|
| `api_key` | `str` | — | Tenant key (`nxs_…`). Scoped to one **environment**. |
| `base_url` | `str` | `https://services.inverge.net` | API origin. |
| `realtime_url` | `str` | `base_url` | Socket.IO origin, if different. |
| `timeout` | `float` | `10.0` | Per-request timeout (seconds). |
| `max_retries` | `int` | `2` | Retries for *safe* requests (ingest, reads). |
| `retry_backoff` | `float` | `0.25` | Base backoff; doubles per attempt. |
| `default_headers` | `dict` | `{}` | Merged into every request. |
| `app_version` / `release` / `os_type` / `os_version` | `str` | — | Default telemetry context. |
| `default_properties` | `dict` | `{}` | Merged into every event's properties. |
| `batch` | `bool` | `True` | Buffer events/logs, ship on a background thread. |
| `flush_interval` | `float` | `5.0` | Seconds between background flushes. |
| `max_batch` | `int` | `50` | Max items per batched request. |
| `max_queue` | `int` | `10000` | Buffer capacity; excess is dropped and counted. |
| `silent` | `bool` | `False` | Never raise — log the failure and return empty. |
| `debug` | `bool` | `False` | Verbose SDK logging on the `nexus` logger. |
| `user_agent` | `str` | `inverge-nexus-python/<v>` | Override the UA. |

**Environment variables**: `NEXUS_API_KEY`, `NEXUS_BASE_URL`,
`NEXUS_REALTIME_URL`, `NEXUS_TIMEOUT`, `NEXUS_MAX_RETRIES`,
`NEXUS_APP_VERSION`, `NEXUS_RELEASE`, `NEXUS_BATCH`, `NEXUS_FLUSH_INTERVAL`,
`NEXUS_MAX_BATCH`, `NEXUS_SILENT`, `NEXUS_DEBUG`.

Explicit arguments are layered **on top of** the environment, so a staging
deploy can set `NEXUS_BASE_URL` and repoint a client whose key is passed in
code. Precedence, lowest first: environment → keyword options → `api_key`.

---

## 4. Three ways to use it

**A client object** — explicit, injectable, trivially testable. Best in
libraries and larger services.

```python
client = NexusClient("nxs_live_xxx")
client.events.track("x")
```

**The module-level default** — one process-wide client. Best in scripts,
workers, management commands and small services.

```python
nexus.init("nxs_live_xxx")
nexus.track("x")
nexus.events.track("x")        # resources work at module level too
```

**A framework integration** — the default client plus per-request identity,
request events and error capture, wired into the framework's lifecycle. See
below.

All three share the same ambient context, so they mix freely.

---

## 5. Framework guides

### Django

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "nexus.integrations.django",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "nexus.integrations.django.NexusMiddleware",   # high, but below auth if you
    ...,                                           # want request.user resolved
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

NEXUS = {
    "API_KEY": os.environ["NEXUS_API_KEY"],
    "RELEASE": os.environ.get("GIT_SHA"),
    "TRACK_REQUESTS": True,     # one event per request
    "TRACK_SESSIONS": False,    # also refresh the Nexus session (extra call)
    "CAPTURE_ERRORS": True,     # report unhandled exceptions
    "LOGGING": True,            # forward Django logging to Nexus logs
    "LOG_LEVEL": "INFO",
    "IGNORE_PATHS": ["/health", "/static/"],
    "SAMPLE_RATE": 1.0,
}
```

Flat settings (`NEXUS_API_KEY`, `NEXUS_BASE_URL`, …) work too, which suits
`django-environ` setups.

Then anywhere in the project:

```python
import nexus

def checkout(request):
    nexus.track("checkout_started", {"cart_total": total})     # attributed to request.user
    if nexus.is_enabled("new_checkout"):
        ...
    return render(request, "checkout.html")
```

Verify the wiring end to end:

```bash
python manage.py nexus_test
```

The middleware puts `request.user.pk` into the ambient context, so telemetry
raised deep in a service layer — with no access to the request — is still
attributed to the right person.

### Flask

```python
from flask import Flask
from nexus.integrations.flask import NexusFlask

app = Flask(__name__)
app.config.update(
    NEXUS_API_KEY=os.environ["NEXUS_API_KEY"],
    NEXUS_TRACK_REQUESTS=True,
    NEXUS_CAPTURE_ERRORS=True,
    NEXUS_LOGGING=True,
)
nexus_ext = NexusFlask(app)          # or NexusFlask().init_app(app) in a factory

@app.get("/orders/<int:order_id>")
def show(order_id):
    nexus_ext.client.events.track("order_viewed", {"id": order_id})
    return {"ok": True}
```

The user is read from `flask_login`'s `current_user` when installed; override
with `NexusFlask(app, distinct_id_resolver=lambda req: session.get("uid"))`.

### FastAPI / Starlette

```python
from fastapi import Depends, FastAPI
from nexus.integrations.fastapi import NexusMiddleware, get_nexus, lifespan_for

app = FastAPI(lifespan=lifespan_for())           # flushes on shutdown
app.add_middleware(NexusMiddleware, api_key=os.environ["NEXUS_API_KEY"])

@app.get("/orders/{order_id}")
async def show(order_id: int, nexus=Depends(get_nexus)):
    nexus.events.track("order_viewed", {"id": order_id})      # buffered, non-blocking
    return {"ok": True}
```

The event records the **route template** (`/orders/{order_id}`), not the
expanded path, so your analytics group properly.

Fully async? Pass an `AsyncNexusClient` and the middleware awaits it:

```python
from nexus.aio import AsyncNexusClient

client = AsyncNexusClient(os.environ["NEXUS_API_KEY"])
app.add_middleware(NexusMiddleware, client=client)
app.router.lifespan_context = lifespan_for(client)
```

### Celery

```python
from celery import Celery
from nexus.integrations.celery import install_celery, header_context

app = Celery("worker")
install_celery(app, api_key=os.environ["NEXUS_API_KEY"])
```

Every task run becomes a `celery_task` event with its duration and state;
failures become Nexus errors; the worker flushes on shutdown.

To carry the user from a web request into the task:

```python
send_receipt.apply_async(args=[order.id], headers=header_context())
```

You can also route *all* Nexus delivery through your own broker:

```python
from nexus.integrations.celery import CeleryDispatcher, nexus_delivery_task

delivery = nexus_delivery_task(app)
queued = client.with_dispatcher(CeleryDispatcher(delivery))
queued.events.track("order_placed")      # returns immediately
```

### Any WSGI app

Pyramid, Bottle, CherryPy, Falcon, web2py, bare `wsgiref` — anything:

```python
from nexus.integrations.wsgi import NexusWSGIMiddleware

app = NexusWSGIMiddleware(app, api_key="nxs_live_xxx")
```

### Any ASGI app

Quart, Litestar, Sanic (ASGI mode), Falcon ASGI, bare Starlette:

```python
from nexus.integrations.asgi import NexusASGIMiddleware

app = NexusASGIMiddleware(app, api_key="nxs_live_xxx")
```

Both middlewares accept `client`, `api_key`, `distinct_id_resolver`,
`track_requests`, `track_sessions`, `capture_errors`, `event_name`,
`ignore_paths`, `sample_rate` and `record_query`.

### Plain scripts & workers

```python
import nexus

nexus.init("nxs_live_xxx")      # installs excepthooks + an atexit flush

nexus.identify("batch_job")
nexus.track("import_started", {"file": path})
...
nexus.flush()
```

Uncaught exceptions (main thread *and* worker threads) are reported
automatically; pass `capture_errors=False` to `init()` to opt out.

Send your existing logging to Nexus with no call-site changes:

```python
import logging, nexus

nexus.install_logging(level=logging.INFO)     # attaches NexusLogHandler to root
logging.getLogger(__name__).info("worker started")
logging.getLogger(__name__).exception("charge failed")   # → log + grouped error
```

---

## 6. Identity & context

Telemetry is correlated by `distinct_id` (who) and `session_key` (which
journey). Rather than passing those to every call, bind them once:

```python
from nexus import context

with context.scope(distinct_id="user_123", url="/checkout", properties={"tier": "pro"}):
    nexus.track("order_placed", {"total": 42})     # carries user_123 + tier=pro
    nexus.get_client().errors.capture("payment declined")
```

`contextvars` back this, so it is correct per-thread **and** per-asyncio-task —
concurrent requests never leak into each other. The framework integrations bind
it for you on every request.

```python
nexus.identify("user_123", email="a@b.com")   # local context + server-side upsert
nexus.identify("user_123", remote=False)      # local only

context.current().distinct_id                 # read it back
context.reset()                               # on logout: new session, same device
context.clear()                               # drop everything (tests, teardown)
```

Precedence for every field, lowest first: **client config → ambient context →
explicit keyword argument**.

---

## 7. Sessions

```python
client.sessions.identify("user_123", email="a@b.com", name="Ada",
                         traits={"plan": "pro"})

result = client.sessions.track(distinct_id="user_123", device_key="dev_abc",
                               session_key="sess_abc", country="IQ")
result["sessionId"]
```

`track()` fills every omitted field from the ambient context.

---

## 8. Events

```python
client.events.track("order_placed", {"total": 42.0, "currency": "USD"})
client.events.track("signup", plan="pro")          # kwargs become properties

# A batch sharing one identity, sent immediately:
client.events.batch(
    [{"name": "view", "properties": {"sku": "A1"}},
     {"name": "click", "properties": {"sku": "A1"}}],
    distinct_id="user_1",
)

client.events.flush()
```

`track()` is **buffered** by default: it appends to an in-memory queue that a
background thread ships every `flush_interval` seconds or every `max_batch`
items, grouping by identity into one request. It costs microseconds in a
request handler. `batch()` always sends immediately and returns the number
written.

Context keys accepted per call: `distinct_id`, `session_key`, `device_key`,
`release`, `app_version`, `os_type`, `os_version`, `browser`, `timestamp`.

---

## 9. Logs

```python
client.logs.info("payment started", source="checkout")
client.logs.error("charge failed", context={"code": "declined"})
client.logs.warn("retrying", attempt=2)                 # kwargs become context
client.logs.log("fatal", "unrecoverable")

client.logs.batch([{"level": "info", "message": "a"}], distinct_id="user_1")
```

Levels: `trace`, `debug`, `info`, `warn`, `error`, `fatal` (`warning` and
`critical` are aliases). Buffered like events.

**Prefer the logging handler** in a real app — it captures what you already
write:

```python
import logging
from nexus import NexusLogHandler

logging.getLogger().addHandler(NexusLogHandler(client, level=logging.INFO))
```

It maps Python levels to Nexus levels, turns `extra={...}` into log context,
and sends records carrying `exc_info` to error monitoring as well.

Two things it will *not* forward, so attaching it to the root logger is safe:
the SDK's own `nexus` logger, and anything logged **during** a Nexus delivery.
That second guard matters — `httpx` logs `HTTP Request: POST …` at INFO on
every call, so without it the handler would log the SDK's own traffic, whose
delivery logs again, forever.

---

## 10. Errors

```python
try:
    charge(order)
except PaymentError as exc:
    client.errors.capture_exception(exc, context={"order": order.id})

# Inside an `except` block the exception is optional:
except PaymentError:
    client.errors.capture_exception()

# Or report without an exception object:
client.errors.capture("Payment gateway timeout",
                      type="GatewayTimeout", level="error",
                      fingerprint="gateway-timeout")
```

`capture_exception` derives the type, message and a structured stack the
console renders — **throw-site first** — and walks `__cause__` / `__context__`
so `raise X from Y` shows both.

Options: `type`, `level`, `handled`, `fingerprint`, `stack`, `context`,
`release`, `url`, plus the usual identity keys.

Uncaught exceptions are reported automatically by `nexus.init()` (scripts and
workers) and by every framework integration (requests and tasks).

---

## 11. Feature flags

```python
flags = client.flags.evaluate("user_1", {"plan": "pro"})
flags.is_enabled("new_checkout")        # bool
flags.variant("paywall")                # "variant_b" | None
flags.payload("paywall")                # attached JSON
flags.flags                             # {key: bool | variant}

# One-liners (each performs one evaluation call):
client.flags.is_enabled("new_checkout", "user_1", {"plan": "pro"})
client.flags.variant("paywall", "user_1")
client.flags.payload("paywall", "user_1")
```

Need several flags for one user? Call `evaluate()` once and query the result —
that's a single round trip.

`distinct_id` falls back to the ambient context, so inside a request you can
just write `nexus.is_enabled("new_checkout")`.

---

## 12. Remote Config

Typed, publishable parameters whose conditions (platform, version, country,
percentile, **custom attributes**) are evaluated server-side.

```python
rc = client.remote_config
rc.set_defaults({"welcome": "Hi", "max_items": 10})

config = rc.fetch({"user_properties": {"governorate": "Duhok"}})
config.get_string("welcome")
config.get_bool("feature_on")
config.get_number("fee_percent")
config.get_json("theme")
config.source_of("phone_number")        # which condition supplied it

# Or the one-liners:
phone = rc.get("phone_number", "+9640000000000",
               {"user_properties": {"governorate": "Erbil"}})
everything = rc.all()                   # flat {key: value}
```

Reads are cached in-process for 30 s and revalidated with the previous ETag, so
a hot path can call `get()` freely (`rc.cache_ttl = 0` or `fresh=True` to
bypass, `rc.invalidate()` to drop it). If a fetch fails while `silent=True`,
the last good snapshot keeps serving.

Context keys: `app_instance_id`, `app_version`, `app_build`, `platform`,
`os_version`, `country`, `language`, `first_open_time`, `user_properties`.
`user_properties` values must be primitives and match condition values exactly.
`platform` defaults to `"server"` — pass the real one (`"ios"`, `"android"`,
`"web"`) when you are resolving config *on behalf of* a device, or
platform conditions won't match.

**Writing** (needs a key with write access — changes edit the draft and only go
live on publish):

```python
rc.set_parameter("fee_percent", 2.5, description="Checkout fee")
rc.delete_parameter("legacy_flag")
rc.publish("raise the fee")

rc.set("phone_number", "+9647500000000")     # set + publish in one step
```

---

## 13. Realtime

**Emitting** (server → clients) is plain HTTP:

```python
from nexus import RoomMessage

client.realtime.emit("orders:42", "status", {"state": "shipped"})
client.realtime.emit("orders:42", ["status", "updated"], {"state": "shipped"})
client.realtime.emit(RoomMessage("orders:42", "status", {"state": "shipped"}))

client.realtime.broadcast([
    RoomMessage("room:a", "ping", {"n": 1}),
    RoomMessage("room:b", "ping", {"n": 2}),
])
client.realtime.emit_to_rooms(["orders:1", "orders:2"], "location", payload)
```

Room management and payload schemas:

```python
client.realtime.register_room("orders", type="standard")
client.realtime.rooms()
client.realtime.delete_room(room_id)
client.realtime.link(room_id, related_id)
client.realtime.unlink(room_id, related_id)
client.realtime.related("orders")

client.realtime.set_schema(room_id, {"type": "object", "required": ["state"]})
client.realtime.enable_schema(room_id, True)
client.realtime.schema(room_id)
client.realtime.clear_schema(room_id)
```

**Listening** needs `python-socketio`:

```python
from nexus import RealtimeSocket

socket = RealtimeSocket(client)
socket.connect()
socket.join("orders:42")

@socket.on("status")
def on_status(data):
    print("order status:", data)

socket.wait()        # block this thread; rooms are re-joined after reconnects
```

> Rooms are **environment-scoped**. The key that emits and the key that joins
> must belong to the same environment — a mismatch looks exactly like silence.

---

## 14. Deep links & attribution

```python
client.links.attribute("install", click_id="abc", name="summer_sale",
                       platform="ios", distinct_id="user_1",
                       properties={"campaign": "promo"})

client.links.install(click_id="abc")
client.links.open(name="summer_sale")
client.links.reengagement(distinct_id="user_1")
```

Types: `install`, `open`, `reengagement`, `uninstall`, `in_app`. Options:
`name`, `click_id`, `distinct_id`, `device_id`, `session_key`, `platform`,
`os_type`, `country`, `revenue`, `properties`.

---

## 15. Surveys

Headless: Nexus handles targeting, sampling and frequency capping; you render.

```python
surveys = client.surveys.active(distinct_id="user_1", properties={"plan": "pro"})

client.surveys.respond(surveys[0]["id"], {"q1": 9}, distinct_id="user_1")
client.surveys.complete(survey_id, {"q1": 9, "q2": "Great"})
client.surveys.dismiss(survey_id)
```

`respond()` returns `{id, completed}` — pass that `id` back as `response_id` to
keep appending to the same partial response.

---

## 16. Push notifications

Transactional sends to users, saved segments or an ad-hoc device filter.
Campaigns, provider credentials and templates live in the console.

```python
result = (
    client.push.notification()
        .title("Your order shipped")
        .body("Track it in the app")
        .image("https://cdn.example.com/box.png")
        .data(screen="/orders/42")
        .button("track", "Track", url="https://x/track")
        .ios_badge(1)
        .android_visibility("public")
        .to_segments(["vip", "active_7d"])
        .send()
)
result["sent"], result["failed"], result["recipients"]
```

Targeting (combines as a union): `.to_users([...])` / `.to_user(id)`,
`.to_segments([...])` / `.to_segment(id)`, `.where(platform="ios", lang="en")`,
`.to_all()` (overrides the rest).

Per-platform options: `ios_badge`, `ios_relevance_score`,
`ios_interruption_level`, `ios_subtitle`, `android_visibility`,
`android_large_icon`, `android_big_picture`, `android_accent_color`, plus
`.option(key, value)` / `.options({...})` for anything newer.

Quick sends without the builder:

```python
client.push.send(["user_1", "user_2"], title="Welcome 👋")
client.push.send_to_user("user_1", title="Welcome 👋", data={"screen": "/home"})
client.push.send_to_segments(["vip"], title="VIP drop")
```

Device tokens (usually the client SDK's job):

```python
client.push.register_token(token, "android", distinct_id="user_1", tags={"tier": "gold"})
client.push.unregister(token)
```

`.to_dict()` on the builder returns the payload without sending — handy in
tests.

---

## 17. In-app messages

```python
messages = client.inapp.active(distinct_id="user_1")
client.inapp.impression("msg_1", distinct_id="user_1")
client.inapp.click("msg_1", "cta", distinct_id="user_1")
client.inapp.dismiss("msg_1", distinct_id="user_1")
```

---

## 18. Live Activities

A live, updating view of an in-progress event on the iOS Lock Screen / Dynamic
Island and as an Android live notification — driven from your backend.

```python
(client.live_activities.activity("DeliveryAttributes", "order_42")
    .title("Order #42").status("Preparing").progress(20)
    .to_user("user_1").priority(10)
    .start())

(client.live_activities.activity("order_42")
    .status("On the way").progress(70).priority(5)
    .update())

(client.live_activities.activity("order_42")
    .status("Delivered").dismiss_at("2026-01-01T12:00:00Z")
    .end())
```

`activity(type, id)` starts one; `activity(id)` updates or ends it. Content
state: `title/subtitle/body/status/progress`, plus `.set(k, v)` / `.state({})`.
Targeting: `.to_user(s)` for a per-order activity, `.shared()` for one everyone
watches. Priority 5 is routine (unmetered), 10 is immediate (metered).

> **iOS** needs the APNs key configured in the console (Live Activities cannot
> go through FCM) and a Widget Extension in the app. **Android** renders a live
> notification with no extra setup.

---

## 19. Session replay

Replay is recorded by the browser and mobile SDKs. The Python resource exists
for proxying those chunks through your own backend:

```python
client.replay.ingest(recording_id, rrweb_events, distinct_id="user_1",
                     href="/checkout", width=1440, height=900)
```

`recording_id` must stay stable across every chunk; at most 5000 events per
call.

---

## 20. Voice (CPaaS)

A call is a **session** with one or more **legs**. The tenant is always the
environment behind your API key — grants, rooms and caller-ID are decided
server-side.

```python
session = client.voice.create_call(direction="outbound", recording_mode="mixed",
                                   metadata={"order": "42"})
session_id = session["session"]["id"]

# A WebRTC leg returns a media join bundle for the client to join with
# ({room, region, access, turn} — hand `access` + `turn` to the device SDK):
agent = client.voice.add_leg(session_id, "agent", "webrtc", identity_id="agent_7")
join = agent["join"]
join["room"], join["access"], join["turn"]
leg_id = agent["leg"]["id"]

# A PSTN leg is dialled by the media plane:
callee = client.voice.add_leg(session_id, "callee", "pstn",
                              address="+9647500000000", caller_id="+9647400000000")
```

> `add_leg` answers **HTTP 200 with an `error` key** for expected failures —
> `call_blocked` (fraud policy), `no_outbound_trunk`, `media_unavailable` —
> rather than a 5xx. Check for it before assuming the leg is live.

In-call control:

```python
client.voice.ring(leg_id); client.voice.answer(leg_id)
client.voice.hold(leg_id); client.voice.resume(leg_id)
client.voice.hangup(leg_id, reason="completed")
client.voice.dtmf(session_id, leg_id, "5")

client.voice.transfer_blind(session_id, leg_id, "pstn", target_address="+964...")
client.voice.transfer_warm_start(session_id, other_leg, "app", target_identity_id="agent_9")
client.voice.transfer_warm_complete(other_leg, initiator_leg)
client.voice.transfer_warm_cancel(other_leg, consult_leg)

client.voice.mute(leg_id, True)
client.voice.remove(session_id, leg_id)
```

IVR, devices, presence and quality:

```python
flow = client.voice.ivr_start("flow_main")
client.voice.ivr_input(flow["sessionId"], "2")      # → next instruction
client.voice.ivr_advance(flow["sessionId"])

client.voice.register_device("agent_7", "device_1", "android", fcm_token=token)
client.voice.presence("agent_7", "device_1", "ONLINE")

client.voice.report_quality(session_id, leg_id=leg_id, rtt_ms=38, mos=4.1)
```

Recordings have **no permanent URL** — the console mints a short-lived access
token, and the SDK turns it into a link:

```python
url = client.voice.recording_download_url(access_token, inline=True)
```

---

## 21. Async client

`AsyncNexusClient` mirrors the entire surface. Request bodies come from the
same shared definitions, so the two clients can never drift apart.

```python
from nexus.aio import AsyncNexusClient

async with AsyncNexusClient("nxs_live_xxx") as nexus:
    await nexus.identify("user_123")
    nexus.events.track("order_placed", {"total": 42})     # buffered: no await
    await nexus.realtime.emit("orders:42", "status", {"state": "shipped"})

    flags = await nexus.flags.evaluate("user_123")
    config = await nexus.remote_config.fetch()
    await nexus.push.notification().title("Hi").to_user("user_123").send()
```

Only buffering is synchronous (it just appends to a queue); everything that
touches the network is a coroutine. The transport is `httpx` → `aiohttp` → a
thread-pool wrapper around the stdlib, whichever is available — the event loop
is never blocked.

---

## 22. Batching, flushing & shutdown

```python
client.flush()                 # ship everything buffered, now
client.flush(timeout=2.0)
client.pending                 # how many items are waiting
client.close()                 # flush + release (idempotent)

with NexusClient("nxs_live_xxx") as client:
    client.events.track("x")   # flushed on exit
```

The SDK registers an `atexit` flush, so a normal interpreter shutdown ships
what's queued. Call `flush()` explicitly before `os._exit`, a hard container
kill, or at the end of a short-lived job.

**Forking (gunicorn, uWSGI, Celery prefork):** threads don't survive `fork()`.
The SDK notices the PID change and restarts its worker with an empty buffer in
each child, so a client built in the master process keeps working instead of
silently going quiet. Building the client *after* fork (in a worker hook) is
still the cleanest arrangement.

---

## 23. Errors & retries

Every failure is a subclass of `NexusError`:

```
NexusError
├── ConfigurationError      bad/missing configuration
├── TransportError          DNS, connection, TLS, timeout
└── ApiError                non-2xx (status, code, message, details)
    ├── AuthenticationError 401
    ├── PermissionDeniedError 403
    ├── NotFoundError       404
    ├── ValidationError     400 / 422
    ├── RateLimitError      429 (retry_after)
    └── ServerError         5xx
```

```python
from nexus import ApiError, NexusError

try:
    client.push.send_to_user("u_1", title="Hi")
except ApiError as exc:
    log.warning("nexus %s %s: %s", exc.status, exc.code, exc.message)
except NexusError:
    pass
```

Prefer telemetry that can never break a request? Set `silent=True` — failures
are logged to the `nexus` logger and calls return empty values. Background
delivery (buffered events/logs, `client.background()`) is always silent.

**Retries** apply only to requests that are safe to repeat — telemetry ingest,
flag evaluation, Remote Config fetch, GETs — on network errors, 429 and 5xx,
with exponential backoff plus jitter and `Retry-After` support. Push sends,
voice control and realtime emits are **never** retried automatically, because a
duplicate is worse than a failure.

---

## 24. Transports & dispatchers

A **transport** decides how bytes move; a **dispatcher** decides when.

```python
from nexus import NexusClient, RequestsTransport, UrllibTransport

client = NexusClient("nxs_...", transport=RequestsTransport(my_session))
client = NexusClient("nxs_...", transport=UrllibTransport())      # force stdlib
```

```python
bg = client.background()                  # every call fire-and-forget
bg.push.send_to_user("u_1", title="Hi")   # returns None immediately

queued = client.with_dispatcher(my_dispatcher)   # e.g. Celery, RQ, SQS
```

Implement `Transport.send(...)` or `Dispatcher.dispatch(request)` to plug in
your own.

---

## 25. Testing

The transport seam makes assertions easy — no network, no mocking library:

```python
from nexus import NexusClient
from nexus.transport import RawResponse, Transport

class FakeTransport(Transport):
    def __init__(self):
        self.requests = []
    def send(self, method, url, headers, body, timeout):
        self.requests.append((method, url, body))
        return RawResponse(200, b"{}")

transport = FakeTransport()
client = NexusClient("nxs_test", transport=transport, batch=False)
client.events.track("order_placed", {"total": 42})

assert transport.requests[0][1].endswith("/partner/events")
```

Use `batch=False` in tests so sends are synchronous, and
`nexus.context.clear()` between cases. The builders' `.to_dict()` lets you
assert a push payload without sending it.

Run the SDK's own suite:

```bash
PYTHONPATH=.:tests python -m unittest discover -s tests -t .
```

---

## 26. Low-level requests

For an endpoint without a dedicated method:

```python
from nexus._payloads import Request

body = client.request(Request("POST", "/partner/events", {"events": [...]}))
body = client.request(Request("GET", "/partner/rooms", retry=True))
```

`Request(method, path, json=None, headers={}, retry=False)` — `retry=True`
opts into the retry policy. The return value is the decoded JSON body (or
`None`).

---

## License

MIT © Inverge
