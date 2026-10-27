# Nexus Python SDK

`inverge-nexus` is the official Python client for the **Inverge Nexus**
platform — every product, correlated to a single user journey, with **sync and
asyncio** clients and first-class **Django**, **Flask**, **FastAPI/Starlette**
and **Celery** integrations, plus generic **WSGI** and **ASGI** middleware.

- PyPI: `inverge-nexus` · import name `nexus`
- Python **3.8+** · **zero required dependencies**

## Documentation

Installation, configuration, framework guides and the full API reference live
on the documentation site:

- **Guide** — <https://docs.inverge.net/sdks/python>
- **API reference** — <https://docs.inverge.net/sdks/python/reference>

## What it covers

- **[Identity & sessions](https://docs.inverge.net/sdks/python/reference/context)** — bind the current user and journey once per request or task.
- **[Events](https://docs.inverge.net/sdks/python/reference/events)** — product analytics sent from your backend.
- **[Logs](https://docs.inverge.net/sdks/python/reference/logs)** — structured, leveled logs, plus a `logging` handler.
- **[Errors](https://docs.inverge.net/sdks/python/reference/errors)** — exceptions with context, reported to Nexus Errors.
- **[Feature flags](https://docs.inverge.net/sdks/python/reference/flags)** — evaluate flags per user on the server.
- **[Remote Config](https://docs.inverge.net/sdks/python/reference/remote-config)** — read remotely managed values, with targeting.
- **[Realtime](https://docs.inverge.net/sdks/python/reference/realtime)** — emit to rooms and listen to room traffic.
- **[Deep links & attribution](https://docs.inverge.net/sdks/python/reference/links)** — create and resolve Nexus links.
- **[Surveys](https://docs.inverge.net/sdks/python/reference/surveys)** — manage and read in-product surveys.
- **[Push notifications](https://docs.inverge.net/sdks/python/reference/push)** — send push to users, devices and tags.
- **[In-app messages](https://docs.inverge.net/sdks/python/reference/inapp)** — trigger in-app messages for users.
- **[Live Activities](https://docs.inverge.net/sdks/python/reference/live-activities)** — start, update and end Live Activities from your server.
- **[Session replay](https://docs.inverge.net/sdks/python/reference/replay)** — forward replay chunks recorded by the browser and mobile SDKs through your own backend.
- **[Voice (CPaaS)](https://docs.inverge.net/sdks/python/reference/voice)** — place and control calls: WebRTC and PSTN legs, IVR, transfer, conference, recordings.
- **[Shield](https://docs.inverge.net/products/shield/verify)** — verify a request really comes from your app on a real device.
- **[Telescope](https://docs.inverge.net/products/telescope/python)** — request-journey tracing for requests, Celery tasks and commands,
  with N+1, duplicate and slow queries flagged.

## Links

- Console — <https://nexus.inverge.net>
- Site — <https://inverge.net>
- Source — <https://github.com/Inverge-team/nexus-python>

MIT licensed. © Inverge.
