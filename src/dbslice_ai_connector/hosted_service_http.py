"""Strict HTTP helpers for communicating with the hosted dbsliceAI service."""

from __future__ import annotations

import json
from http.client import HTTPResponse
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 64 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def normalize_server_origin(server_url: str, *, purpose: str) -> str:
    parsed = urlsplit(server_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"server URL for connector {purpose} must be an origin")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError(
            f"unencrypted connector {purpose} is allowed only on loopback"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def open_without_redirects(request: Request, timeout: float) -> HTTPResponse:
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def json_post(url: str, payload: object, *, user_agent: str) -> Request:
    return Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
        method="POST",
    )


class BoundedJsonResponse:
    def __init__(self, error_type: type[RuntimeError], label: str) -> None:
        self.error_type = error_type
        self.label = label

    def read(self, response: HTTPResponse) -> dict[str, Any]:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise self.error_type(f"{self.label} response was too large")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise self.error_type(
                f"{self.label} response was not valid JSON"
            ) from error
        if not isinstance(value, dict):
            raise self.error_type(f"{self.label} response had an invalid shape")
        return value

    def required_string(self, value: dict[str, Any], name: str) -> str:
        item = value.get(name)
        if not isinstance(item, str) or not item:
            raise self.error_type(
                f"{self.label} response field {name} was missing or invalid"
            )
        return item

    def error_code(self, response: HTTPResponse) -> str | None:
        try:
            value = self.read(response)
        except RuntimeError:
            return None
        code = value.get("error")
        return code if isinstance(code, str) else None
