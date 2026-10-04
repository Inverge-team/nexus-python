"""FastAPI: `pip install fastapi uvicorn && uvicorn examples.fastapi_app:app`."""

import os

from fastapi import Depends, FastAPI

from nexus.integrations.fastapi import NexusMiddleware, get_nexus, lifespan_for

app = FastAPI(lifespan=lifespan_for())         # flushes buffered telemetry on shutdown
app.add_middleware(
    NexusMiddleware,
    api_key=os.environ.get("NEXUS_API_KEY"),
    track_requests=True,
    capture_errors=True,
)


@app.get("/orders/{order_id}")
async def show_order(order_id: int, nexus=Depends(get_nexus)):
    # Buffered — no await, no network on the request path. The recorded event
    # path is the route template "/orders/{order_id}", not "/orders/42".
    nexus.events.track("order_viewed", {"id": order_id})
    return {"order": order_id}


@app.post("/orders/{order_id}/ship")
async def ship(order_id: int, nexus=Depends(get_nexus)):
    nexus.realtime.emit("orders:{0}".format(order_id), "status", {"state": "shipped"})
    return {"ok": True}


@app.get("/boom")
async def boom():
    raise RuntimeError("reported to Nexus, then re-raised")
