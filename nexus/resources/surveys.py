"""In-product surveys — fetch what a user is eligible for, submit answers."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .. import _payloads as p
from .base import Resource

__all__ = ["Surveys"]


class Surveys(Resource):
    """``client.surveys`` — headless: you render, Nexus targets and stores."""

    def active(
        self,
        *,
        distinct_id: Optional[str] = None,
        device_key: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        device_type: Optional[str] = None,
        os_type: Optional[str] = None,
    ) -> List[Any]:
        """Surveys this user may see (targeting, sampling and capping applied)."""
        fields = self._context(distinct_id=distinct_id, device_key=device_key, os_type=os_type)
        fields["properties"] = dict(properties) if properties else None
        fields["deviceType"] = device_type
        return self._list(self._send(p.surveys_active(fields)), "surveys")

    def respond(
        self,
        survey_id: str,
        answers: Optional[Mapping[str, Any]] = None,
        *,
        completed: Optional[bool] = None,
        dismissed: Optional[bool] = None,
        response_id: Optional[str] = None,
        iteration_key: Optional[str] = None,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
        app_version: Optional[str] = None,
        country: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a response (partial or complete); answers keyed by question id.

        Returns ``{id, completed}``. Pass the returned ``id`` back as
        ``response_id`` to keep appending to the same response.
        """
        fields = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
            app_version=app_version,
            country=country,
            url=url,
        )
        fields.update(
            {
                "completed": completed,
                "dismissed": dismissed,
                "responseId": response_id,
                "iterationKey": iteration_key,
            }
        )
        return self._dict(self._send(p.surveys_respond(survey_id, answers, fields)))

    def complete(self, survey_id: str, answers: Mapping[str, Any], **options: Any) -> Dict[str, Any]:
        """Submit a completed response in one call."""
        options["completed"] = True
        return self.respond(survey_id, answers, **options)

    def dismiss(self, survey_id: str, **options: Any) -> Dict[str, Any]:
        """Record that the user dismissed the survey."""
        options["dismissed"] = True
        return self.respond(survey_id, None, **options)
