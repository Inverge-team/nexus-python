"""A plain Python script — no framework.

    NEXUS_API_KEY=nxs_live_xxx python examples/script.py
"""

import logging
import time

import nexus

logging.basicConfig(level=logging.INFO)

# init() reads NEXUS_API_KEY when no key is passed, and installs excepthooks so
# an uncaught crash in this script still reaches the console.
client = nexus.init()

# Send everything the script already logs to Nexus logs as well.
nexus.install_logging(level=logging.INFO)
log = logging.getLogger("importer")

nexus.identify("nightly_importer", traits={"kind": "job"})

with nexus.context.scope(properties={"job": "nightly-import"}):
    nexus.track("import_started", {"source": "s3://bucket/orders.csv"})
    log.info("importing orders")

    rows = 0
    for row in range(250):
        rows += 1
        if rows % 100 == 0:
            nexus.track("import_progress", {"rows": rows})

    # A feature flag decides whether to run the new reconciliation pass.
    if nexus.is_enabled("new_reconciliation", "nightly_importer"):
        log.info("running the new reconciliation pass")

    # Remote Config without a redeploy.
    batch_size = client.remote_config.get_int("import_batch_size", 500)
    log.info("batch size is %s", batch_size)

    try:
        raise ValueError("one row failed to parse")
    except ValueError:
        log.exception("row parse failed")      # → a Nexus log *and* a Nexus error

    nexus.track("import_finished", {"rows": rows, "duration_s": 1})

# A short-lived process must flush before it exits.
nexus.flush()
print("done — check the Nexus console")
time.sleep(0)
