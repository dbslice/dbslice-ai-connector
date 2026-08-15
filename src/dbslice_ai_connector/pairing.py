"""Browser-approved pairing with a hosted dbsliceAI product."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

from .hosted_service_http import (
    BoundedJsonResponse,
    json_post,
    normalize_server_origin,
    open_without_redirects,
)


class ConnectorPairingError(RuntimeError):
    """A safe, bounded connector-pairing failure."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


RESPONSE = BoundedJsonResponse(ConnectorPairingError, "Pairing")


@dataclass(frozen=True)
class PendingPairing:
    server_origin: str
    connector_instance_id: str
    refresh_credential: str
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_at: str
    interval_seconds: int


@dataclass(frozen=True)
class PairingResult:
    connector_id: str
    connector_instance_id: str
    refresh_credential: str
    status: str
    enrolled_at: str
    server_origin: str


def generate_connector_instance_id() -> str:
    return f"ci_{secrets.token_urlsafe(18)}"


def generate_refresh_credential() -> str:
    return f"dbr_{secrets.token_urlsafe(32)}"


def _same_origin_url(value: str, server_origin: str, name: str) -> str:
    if not value:
        raise ConnectorPairingError(f"Pairing response field {name} is unavailable")
    parsed = urlsplit(value)
    if f"{parsed.scheme}://{parsed.netloc}" != server_origin:
        raise ConnectorPairingError(f"Pairing response field {name} has an unexpected origin")
    return value


def start_pairing(
    *,
    server_url: str,
    connector_instance_id: str,
    display_name: str,
    refresh_credential: str | None = None,
    timeout_seconds: float = 30,
    open_request: Callable[[Request, float], HTTPResponse] | None = None,
) -> PendingPairing:
    """Create a pending pairing without assigning it to a user."""

    if not connector_instance_id:
        raise ValueError("connector instance ID must be non-empty")
    if not display_name.strip():
        raise ValueError("connector display name must be non-empty")
    credential = refresh_credential or generate_refresh_credential()
    server_origin = normalize_server_origin(server_url, purpose="pairing")
    request = json_post(
        f"{server_origin}/api/connectors/pairings",
        {
            "connectorInstanceId": connector_instance_id,
            "displayName": display_name.strip(),
            "refreshCredentialHash": hashlib.sha256(
                credential.encode("utf-8")
            ).hexdigest(),
        },
        user_agent="dbslice-ai-connector/pairing",
    )
    opener = open_request or open_without_redirects
    try:
        with opener(request, timeout_seconds) as response:
            value = RESPONSE.read(response)
    except HTTPError as error:
        raise ConnectorPairingError(
            "Connector pairing could not be started",
            code=RESPONSE.error_code(error),
        ) from error
    interval = value.get("intervalSeconds")
    if not isinstance(interval, int) or interval < 1 or interval > 60:
        raise ConnectorPairingError("Pairing response has an invalid polling interval")
    verification_uri = _same_origin_url(
        RESPONSE.required_string(value, "verificationUri"),
        server_origin,
        "verificationUri",
    )
    verification_uri_complete = _same_origin_url(
        RESPONSE.required_string(value, "verificationUriComplete"),
        server_origin,
        "verificationUriComplete",
    )
    return PendingPairing(
        server_origin=server_origin,
        connector_instance_id=connector_instance_id,
        refresh_credential=credential,
        device_code=RESPONSE.required_string(value, "deviceCode"),
        user_code=RESPONSE.required_string(value, "userCode"),
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_at=RESPONSE.required_string(value, "expiresAt"),
        interval_seconds=interval,
    )


def poll_pairing(
    pairing: PendingPairing,
    *,
    timeout_seconds: float = 30,
    open_request: Callable[[Request, float], HTTPResponse] | None = None,
) -> PairingResult | None:
    """Poll once, returning credentials metadata only after browser approval."""

    request = json_post(
        f"{pairing.server_origin}/api/connectors/pairings/poll",
        {"deviceCode": pairing.device_code},
        user_agent="dbslice-ai-connector/pairing",
    )
    opener = open_request or open_without_redirects
    try:
        with opener(request, timeout_seconds) as response:
            value = RESPONSE.read(response)
    except HTTPError as error:
        raise ConnectorPairingError(
            "Connector pairing was rejected or expired",
            code=RESPONSE.error_code(error),
        ) from error
    status = RESPONSE.required_string(value, "status")
    if status == "pending":
        return None
    if status != "enrolled":
        raise ConnectorPairingError("Pairing response did not confirm enrollment")
    instance_id = RESPONSE.required_string(value, "connectorInstanceId")
    if instance_id != pairing.connector_instance_id:
        raise ConnectorPairingError(
            "Pairing response did not match this connector installation"
        )
    return PairingResult(
        connector_id=RESPONSE.required_string(value, "connectorId"),
        connector_instance_id=instance_id,
        refresh_credential=pairing.refresh_credential,
        status=status,
        enrolled_at=RESPONSE.required_string(value, "enrolledAt"),
        server_origin=pairing.server_origin,
    )


def wait_for_pairing(
    pairing: PendingPairing,
    *,
    open_request: Callable[[Request, float], HTTPResponse] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> PairingResult:
    """Poll until the user approves the pairing or the server rejects it."""

    while True:
        result = poll_pairing(pairing, open_request=open_request)
        if result is not None:
            return result
        sleep(pairing.interval_seconds)
