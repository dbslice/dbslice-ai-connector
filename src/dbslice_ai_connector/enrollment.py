"""One-time enrollment against a hosted dbsliceAI product."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request

from .hosted_service_http import (
    BoundedJsonResponse,
    json_post,
    normalize_server_origin,
    open_without_redirects,
)


class ConnectorEnrollmentError(RuntimeError):
    """A safe, bounded connector-enrollment failure."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


RESPONSE = BoundedJsonResponse(ConnectorEnrollmentError, "Enrollment")


@dataclass(frozen=True)
class EnrollmentResult:
    connector_id: str
    connector_instance_id: str
    refresh_credential: str
    status: str
    enrolled_at: str
    server_origin: str


def generate_connector_instance_id() -> str:
    return f"ci_{secrets.token_urlsafe(18)}"


def _endpoint(server_url: str) -> tuple[str, str]:
    origin = normalize_server_origin(server_url, purpose="enrollment")
    return origin, f"{origin}/api/connectors/enroll"


def enroll_connector(
    *,
    server_url: str,
    enrollment_token: str,
    connector_instance_id: str,
    timeout_seconds: float = 30,
    open_request: Callable[[Request, float], HTTPResponse] | None = None,
) -> EnrollmentResult:
    """Consume an enrollment token and return the new connector credential."""

    if not enrollment_token:
        raise ValueError("enrollment token must be non-empty")
    if not connector_instance_id:
        raise ValueError("connector instance ID must be non-empty")
    server_origin, enrollment_url = _endpoint(server_url)
    request = json_post(
        enrollment_url,
        {
            "enrollmentToken": enrollment_token,
            "connectorInstanceId": connector_instance_id,
        },
        user_agent="dbslice-ai-connector/enrollment",
    )
    opener = open_request or open_without_redirects
    try:
        with opener(request, timeout_seconds) as response:
            value = RESPONSE.read(response)
    except HTTPError as error:
        raise ConnectorEnrollmentError(
            "Connector enrollment was rejected",
            code=RESPONSE.error_code(error),
        ) from error

    result = EnrollmentResult(
        connector_id=RESPONSE.required_string(value, "connectorId"),
        connector_instance_id=RESPONSE.required_string(
            value, "connectorInstanceId"
        ),
        refresh_credential=RESPONSE.required_string(value, "refreshCredential"),
        status=RESPONSE.required_string(value, "status"),
        enrolled_at=RESPONSE.required_string(value, "enrolledAt"),
        server_origin=server_origin,
    )
    if result.connector_instance_id != connector_instance_id:
        raise ConnectorEnrollmentError(
            "Enrollment response did not match this connector installation"
        )
    if result.status != "enrolled":
        raise ConnectorEnrollmentError("Enrollment response did not confirm enrollment")
    return result
