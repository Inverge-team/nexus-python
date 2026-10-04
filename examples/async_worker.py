"""A fully async worker using AsyncNexusClient."""

import asyncio
import os

from nexus.aio import AsyncNexusClient


async def main() -> None:
    async with AsyncNexusClient(os.environ["NEXUS_API_KEY"]) as nexus:
        await nexus.identify("async_worker", traits={"kind": "job"})

        # Buffered — synchronous because it only appends to a queue.
        nexus.events.track("worker_started")

        flags = await nexus.flags.evaluate("async_worker")
        if flags.is_enabled("parallel_fetch"):
            await asyncio.gather(*(handle(nexus, n) for n in range(5)))
        else:
            for n in range(5):
                await handle(nexus, n)

        config = await nexus.remote_config.fetch()
        print("poll interval:", config.get_int("poll_interval_s", 30))

        await nexus.push.notification().title("Worker finished").to_user("ops").send()
        await nexus.flush()


async def handle(nexus: AsyncNexusClient, n: int) -> None:
    try:
        await nexus.realtime.emit("jobs:{0}".format(n), "progress", {"pct": 100})
        nexus.events.track("job_done", {"n": n})
    except Exception as exc:  # noqa: BLE001 - report then continue
        await nexus.errors.capture_exception(exc, context={"job": n})


if __name__ == "__main__":
    asyncio.run(main())
