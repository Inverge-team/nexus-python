"""``python manage.py nexus_test`` — prove the configuration end to end."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a test event, log line and error to Nexus, then flush."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--distinct-id", default="nexus_test_user")
        parser.add_argument("--skip-error", action="store_true", help="Don't send the test error.")

    def handle(self, *args: Any, **options: Any) -> None:
        import nexus
        from nexus.exceptions import NexusError

        client = nexus.get_client(required=False)
        if client is None:
            self.stderr.write(self.style.ERROR("Nexus is not configured — set NEXUS['API_KEY']."))
            return

        self.stdout.write("Base URL: {0}".format(client.config.http_base))
        distinct_id = options["distinct_id"]

        try:
            client.sessions.identify(distinct_id, traits={"source": "nexus_test"})
            self.stdout.write(self.style.SUCCESS("identify  ok"))
        except NexusError as exc:
            self.stderr.write(self.style.ERROR("identify  failed: {0}".format(exc)))
            return

        client.events.track("nexus_test_event", {"source": "manage.py"}, distinct_id=distinct_id)
        client.logs.info("nexus_test log line", source="manage.py", distinct_id=distinct_id)

        if not options["skip_error"]:
            try:
                raise RuntimeError("Nexus test error — safe to ignore")
            except RuntimeError as exc:
                client.errors.capture_exception(exc, handled=True, context={"source": "manage.py"})
            self.stdout.write(self.style.SUCCESS("error     ok"))

        client.flush(timeout=10.0)
        self.stdout.write(self.style.SUCCESS("flushed — check the Nexus console."))
