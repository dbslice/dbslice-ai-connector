"""Validation helpers for the authoritative connector protocol schemas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
import rfc8785


class ProtocolValidationError(ValueError):
    """Raised when a connector protocol message is invalid."""


def load_protocol_schema(schema_path: Path) -> dict[str, Any]:
    """Load and check one protocol schema document."""

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_protocol_message(
    message: Any,
    *,
    schema: dict[str, Any],
) -> None:
    """Validate one protocol message, including binary payload size semantics."""

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(message), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ProtocolValidationError(details)

    if (
        isinstance(message, dict)
        and message.get("messageType") == "operation.success"
        and message.get("operation") == "readExtractPayload"
    ):
        _validate_extract_payload_size(message["result"])


def _validate_extract_payload_size(payload: dict[str, Any]) -> None:
    if payload["encoding"] == "base64":
        try:
            decoded = base64.b64decode(payload["data"], validate=True)
        except (binascii.Error, ValueError) as error:
            raise ProtocolValidationError("extract payload data is not valid base64") from error

        if len(decoded) != payload["decodedSizeBytes"]:
            raise ProtocolValidationError(
                "decodedSizeBytes does not match the decoded extract payload"
            )

        encoded_size = len(payload["data"].encode("ascii"))
        if encoded_size != payload["encodedSizeBytes"]:
            raise ProtocolValidationError(
                "encodedSizeBytes does not match the base64 extract payload"
            )
        _validate_fingerprint(payload, decoded)
        return

    try:
        encoded = rfc8785.dumps(payload["data"])
    except (rfc8785.FloatDomainError, rfc8785.IntegerDomainError) as error:
        raise ProtocolValidationError(
            "extract payload data cannot be canonicalized as RFC 8785 JSON"
        ) from error
    if len(encoded) != payload["encodedSizeBytes"]:
        raise ProtocolValidationError(
            "encodedSizeBytes does not match canonical JSON extract data"
        )
    _validate_fingerprint(payload, encoded)


def _validate_fingerprint(payload: dict[str, Any], content: bytes) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != payload["fingerprint"]["value"]:
        raise ProtocolValidationError(
            "extract payload fingerprint does not match its content"
        )
