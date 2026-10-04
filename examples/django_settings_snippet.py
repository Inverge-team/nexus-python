"""The Django wiring, as a copy-paste snippet for your settings.py."""

import os

INSTALLED_APPS = [
    # ... your apps ...
    "nexus.integrations.django",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # High in the list so it wraps as much of the stack as possible — but below
    # AuthenticationMiddleware if you want request.user resolved for identity.
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "nexus.integrations.django.NexusMiddleware",
    # ... the rest ...
]

NEXUS = {
    "API_KEY": os.environ.get("NEXUS_API_KEY"),
    "RELEASE": os.environ.get("GIT_SHA"),
    "APP_VERSION": "2.1.0",
    "TRACK_REQUESTS": True,
    "TRACK_SESSIONS": False,   # an extra call per request; on only if no client SDK
    "CAPTURE_ERRORS": True,
    "LOGGING": True,
    "LOG_LEVEL": "INFO",
    "IGNORE_PATHS": ["/health", "/static/", "/admin/jsi18n/"],
    "SAMPLE_RATE": 1.0,
}

# Then, anywhere in the project:
#
#     import nexus
#
#     def checkout(request):
#         nexus.track("checkout_started", {"total": total})   # attributed to request.user
#         if nexus.is_enabled("new_checkout"):
#             ...
#
# Verify the wiring with:  python manage.py nexus_test
