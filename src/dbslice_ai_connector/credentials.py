"""Private persistent storage for connector refresh credentials."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

CREDENTIAL_FORMAT_VERSION = 1


class CredentialFileExistsError(RuntimeError):
    """Raised when enrollment would replace an existing connector identity."""


@dataclass(frozen=True)
class ConnectorCredentials:
    """The minimum persistent identity returned by connector enrollment."""

    server_origin: str
    connector_id: str
    connector_instance_id: str
    refresh_credential: str
    format_version: int = CREDENTIAL_FORMAT_VERSION


def default_credentials_path(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return the platform-neutral default credential-file location."""

    values = os.environ if environ is None else environ
    config_home = values.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else (
        (home or Path.home()) / ".config"
    )
    return root / "dbslice-ai-connector" / "credentials.json"


def credentials_file_exists(path: Path) -> bool:
    """Return true for any existing path, including a dangling symlink."""

    return path.exists() or path.is_symlink()


def write_new_credentials(path: Path, credentials: ConnectorCredentials) -> None:
    """Atomically create a private credential file without replacing one."""

    path = path.expanduser()
    if credentials_file_exists(path):
        raise CredentialFileExistsError(f"Credential file already exists: {path}")

    if path.parent.is_symlink():
        raise PermissionError(
            f"Credential directory must not be a symbolic link: {path.parent}"
        )
    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        if not parent_existed:
            path.parent.chmod(0o700)
        elif path.parent.stat().st_mode & 0o077:
            raise PermissionError(
                f"Credential directory must not be group/world accessible: {path.parent}"
            )

    payload = json.dumps(
        {
            "formatVersion": credentials.format_version,
            "serverOrigin": credentials.server_origin,
            "connectorId": credentials.connector_id,
            "connectorInstanceId": credentials.connector_instance_id,
            "refreshCredential": credentials.refresh_credential,
        },
        separators=(",", ":"),
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            raise CredentialFileExistsError(
                f"Credential file already exists: {path}"
            ) from error
        if os.name == "posix":
            path.chmod(0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_credentials(path: Path) -> ConnectorCredentials:
    """Read and validate the current credential-file format."""

    value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    expected = {
        "formatVersion",
        "serverOrigin",
        "connectorId",
        "connectorInstanceId",
        "refreshCredential",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Credential file has an unsupported shape")
    if value["formatVersion"] != CREDENTIAL_FORMAT_VERSION:
        raise ValueError("Credential file has an unsupported format version")
    for name in expected - {"formatVersion"}:
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError(f"Credential file field {name} must be non-empty")
    return ConnectorCredentials(
        format_version=value["formatVersion"],
        server_origin=value["serverOrigin"],
        connector_id=value["connectorId"],
        connector_instance_id=value["connectorInstanceId"],
        refresh_credential=value["refreshCredential"],
    )
