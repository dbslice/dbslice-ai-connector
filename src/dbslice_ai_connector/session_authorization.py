"""Rotating authorization for one hosted connector WebSocket session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

from .credentials import (
    commit_refresh_rotation,
    prepare_refresh_rotation,
)
from .hosted_service_http import (
    BoundedJsonResponse,
    json_post,
    normalize_server_origin,
    open_without_redirects,
)


class ConnectorSessionAuthorizationError(RuntimeError):
    """A safe, bounded connector-session authorization failure."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


RESPONSE = BoundedJsonResponse(
    ConnectorSessionAuthorizationError,
    "Connector session",
)


@dataclass(frozen=True)
class ConnectorSessionAuthorization:
    websocket_url: str
    connector_instance_id: str
    session_token: str


def _endpoints(server_origin: str) -> tuple[str, str]:
    origin = normalize_server_origin(server_origin, purpose="authorization")
    parsed = urlsplit(origin)
    websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
    websocket_url = urlunsplit(
        (websocket_scheme, parsed.netloc, "/connector/v1", "", "")
    )
    return f"{origin}/api/connectors/session", websocket_url


def exchange_connector_session(
    *,
    server_origin: str,
    connector_id: str,
    connector_instance_id: str,
    refresh_credential: str,
    next_refresh_credential_hash: str,
    timeout_seconds: float = 30,
    open_request: Callable[[Request, float], HTTPResponse] | None = None,
) -> ConnectorSessionAuthorization:
    """Rotate a refresh credential and obtain one short-lived session token."""

    session_url, websocket_url = _endpoints(server_origin)
    request = json_post(
        session_url,
        {
            "connectorId": connector_id,
            "connectorInstanceId": connector_instance_id,
            "refreshCredential": refresh_credential,
            "nextRefreshCredentialHash": next_refresh_credential_hash,
        },
        user_agent="dbslice-ai-connector/session-authorization",
    )
    opener = open_request or open_without_redirects
    try:
        with opener(request, timeout_seconds) as response:
            value = RESPONSE.read(response)
    except HTTPError as error:
        raise ConnectorSessionAuthorizationError(
            "Connector session authorization was rejected",
            code=RESPONSE.error_code(error),
        ) from error

    authorization = ConnectorSessionAuthorization(
        websocket_url=websocket_url,
        connector_instance_id=connector_instance_id,
        session_token=RESPONSE.required_string(value, "sessionToken"),
    )
    return authorization


def authorize_connector_session(
    credentials_path: Path,
    *,
    exchange: Callable[..., ConnectorSessionAuthorization] = exchange_connector_session,
) -> ConnectorSessionAuthorization:
    """Resume or perform one crash-safe refresh rotation and session exchange."""

    credentials, rotation = prepare_refresh_rotation(credentials_path)
    authorization = exchange(
        server_origin=credentials.server_origin,
        connector_id=credentials.connector_id,
        connector_instance_id=credentials.connector_instance_id,
        refresh_credential=credentials.refresh_credential,
        next_refresh_credential_hash=rotation.next_refresh_credential_hash,
    )
    commit_refresh_rotation(credentials_path, rotation)
    return authorization


async def authorize_connector_session_async(
    credentials_path: Path,
) -> ConnectorSessionAuthorization:
    return await asyncio.to_thread(authorize_connector_session, credentials_path)
