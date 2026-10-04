"""Flask: `pip install flask && NEXUS_API_KEY=... flask --app examples/flask_app run`."""

import os

from flask import Flask, jsonify

import nexus
from nexus.integrations.flask import NexusFlask

app = Flask(__name__)
app.config.update(
    NEXUS_API_KEY=os.environ.get("NEXUS_API_KEY"),
    NEXUS_TRACK_REQUESTS=True,     # one event per request
    NEXUS_CAPTURE_ERRORS=True,     # unhandled exceptions → Nexus errors
    NEXUS_LOGGING=True,            # app.logger → Nexus logs
)

nexus_ext = NexusFlask(app)


@app.get("/orders/<int:order_id>")
def show_order(order_id: int):
    # Identity bound by the middleware is already in context, so this event is
    # attributed without passing anything down.
    nexus.track("order_viewed", {"id": order_id})

    if nexus.is_enabled("new_order_page", "user_1"):
        return jsonify(order=order_id, layout="v2")
    return jsonify(order=order_id, layout="v1")


@app.post("/orders/<int:order_id>/ship")
def ship(order_id: int):
    client = nexus_ext.client
    client.realtime.emit("orders:{0}".format(order_id), "status", {"state": "shipped"})
    client.push.send_to_user("user_1", title="Your order shipped", body="Track it in the app")
    return jsonify(ok=True)


@app.get("/boom")
def boom():
    raise RuntimeError("this is reported to Nexus automatically")
