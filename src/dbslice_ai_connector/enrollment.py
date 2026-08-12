"""One-time enrollment against a hosted dbsliceAI product."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

MAX_RESPONSE_BYTES = 64 * 1024


class ConnectorEnrollmentError(RuntimeError):
    """A safe, bounded connector-enrollment failure."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnrollmentResult:
    connector_id: str
    connector_instance_id: str
    refresh_credential: str
    status: str
    enrolled_at: str
    server_origin: str


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def generate_connector_instance_id() -> str:
    return f"ci_{secrets.token_urlsafe(18)}"


def _endpoint(server_url: str) -> tuple[str, str]:
    parsed = urlsplit(server_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("server URL must not contain credentials, query, or fragment")
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("server URL must be an absolute HTTPS URL")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("unencrypted connector enrollment is allowed only on loopback")
    if parsed.path not in {"", "/"}:
        raise ValueError("server URL must contain only the server origin")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return origin, f"{origin}/api/connectors/enroll"


def _response_json(response: HTTPResponse) -> dict[str, Any]:
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ConnectorEnrollmentError("Enrollment response was too large")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConnectorEnrollmentError("Enrollment response was not valid JSON") from error
    if not isinstance(value, dict):
        raise ConnectorEnrollmentError("Enrollment response had an invalid shape")
    return value


def _required_string(value: dict[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise ConnectorEnrollmentError(
            f"Enrollment response field {name} was missing or invalid"
        )
    return item


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
    body = json.dumps(
        {
            "enrollmentToken": enrollment_token,
            "connectorInstanceId": connector_instance_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        enrollment_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "dbslice-ai-connector/enrollment",
        },
        method="POST",
    )
    opener = open_request or (
        lambda candidate, timeout: build_opener(_RejectRedirects()).open(
            candidate,
            timeout=timeout,
        )
    )
    try:
        with opener(request, timeout_seconds) as response:
            value = _response_json(response)
    except HTTPError as error:
        try:
            value = _response_json(error)
            code = value.get("error") if isinstance(value.get("error"), str) else None
        except ConnectorEnrollmentError:
            code = None
        raise ConnectorEnrollmentError(
            "Connector enrollment was rejected",
            code=code,
        ) from error

    result = EnrollmentResult(
        connector_id=_required_string(value, "connectorId"),
        connector_instance_id=_required_string(value, "connectorInstanceId"),
        refresh_credential=_required_string(value, "refreshCredential"),
        status=_required_string(value, "status"),
        enrolled_at=_required_string(value, "enrolledAt"),
        server_origin=server_origin,
    )
    if result.connector_instance_id != connector_instance_id:
        raise ConnectorEnrollmentError(
            "Enrollment response did not match this connector installation"
        )
    if result.status != "enrolled":
        raise ConnectorEnrollmentError("Enrollment response did not confirm enrollment")
    return result
