"""Private persistent storage for connector refresh credentials."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
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


@dataclass(frozen=True)
class PendingCredentialRotation:
    base_refresh_credential_hash: str
    next_refresh_credential: str

    @property
    def next_refresh_credential_hash(self) -> str:
        return _secret_hash(self.next_refresh_credential)


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


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _assert_private_parent(path: Path, *, create: bool) -> None:
    if path.parent.is_symlink():
        raise PermissionError(
            f"Credential directory must not be a symbolic link: {path.parent}"
        )
    parent_existed = path.parent.exists()
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.parent.is_dir():
        raise PermissionError(f"Credential directory is unavailable: {path.parent}")
    if os.name == "posix":
        if create and not parent_existed:
            path.parent.chmod(0o700)
        elif path.parent.stat().st_mode & 0o077:
            raise PermissionError(
                f"Credential directory must not be group/world accessible: {path.parent}"
            )


def _write_payload(descriptor: int, temporary_path: Path, payload: object) -> None:
    os.chmod(temporary_path, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def _create_private_json(path: Path, payload: object) -> None:
    _assert_private_parent(path, create=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        _write_payload(descriptor, temporary_path, payload)
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            raise CredentialFileExistsError(
                f"Credential file already exists: {path}"
            ) from error
    finally:
        temporary_path.unlink(missing_ok=True)


def _replace_private_json(path: Path, payload: object) -> None:
    _assert_private_parent(path, create=False)
    if path.is_symlink() or not path.is_file():
        raise PermissionError(f"Credential file must be a regular file: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        _write_payload(descriptor, temporary_path, payload)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_private_json(path: Path) -> object:
    path = path.expanduser()
    _assert_private_parent(path, create=False)
    if path.is_symlink() or not path.is_file():
        raise PermissionError(f"Credential file must be a regular file: {path}")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise PermissionError(
            f"Credential file must not be group/world accessible: {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _credentials_payload(credentials: ConnectorCredentials) -> dict[str, object]:
    return {
        "formatVersion": CREDENTIAL_FORMAT_VERSION,
        "serverOrigin": credentials.server_origin,
        "connectorId": credentials.connector_id,
        "connectorInstanceId": credentials.connector_instance_id,
        "refreshCredential": credentials.refresh_credential,
    }


def prepare_new_credentials_path(path: Path) -> Path:
    """Validate and prepare a private destination before remote enrollment."""

    path = path.expanduser()
    if credentials_file_exists(path):
        raise CredentialFileExistsError(f"Credential file already exists: {path}")
    _assert_private_parent(path, create=True)
    return path


def write_new_credentials(path: Path, credentials: ConnectorCredentials) -> None:
    """Atomically create a private credential file without replacing one."""

    path = prepare_new_credentials_path(path)
    _create_private_json(path, _credentials_payload(credentials))


def read_credentials(path: Path) -> ConnectorCredentials:
    """Read and validate the current credential-file format."""

    value = _read_private_json(path)
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
        server_origin=value["serverOrigin"],
        connector_id=value["connectorId"],
        connector_instance_id=value["connectorInstanceId"],
        refresh_credential=value["refreshCredential"],
    )


def _rotation_path(credentials_path: Path) -> Path:
    path = credentials_path.expanduser()
    return path.with_name(f".{path.name}.rotation")


def _read_rotation(path: Path) -> PendingCredentialRotation:
    value = _read_private_json(path)
    expected = {"formatVersion", "baseRefreshCredentialHash", "nextRefreshCredential"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Pending credential rotation has an unsupported shape")
    if value["formatVersion"] != 1:
        raise ValueError("Pending credential rotation has an unsupported version")
    base_hash = value["baseRefreshCredentialHash"]
    next_credential = value["nextRefreshCredential"]
    if not isinstance(base_hash, str) or not _is_sha256(base_hash):
        raise ValueError("Pending credential rotation has an invalid base hash")
    if not isinstance(next_credential, str) or not next_credential:
        raise ValueError("Pending credential rotation has an invalid successor")
    return PendingCredentialRotation(base_hash, next_credential)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def prepare_refresh_rotation(
    credentials_path: Path,
    *,
    secret_generator=lambda: f"dbr_{secrets.token_urlsafe(32)}",
) -> tuple[ConnectorCredentials, PendingCredentialRotation]:
    """Durably prepare or resume one refresh-credential rotation."""

    path = credentials_path.expanduser()
    credentials = read_credentials(path)
    rotation_path = _rotation_path(path)
    if credentials_file_exists(rotation_path):
        rotation = _read_rotation(rotation_path)
        current_hash = _secret_hash(credentials.refresh_credential)
        if rotation.base_refresh_credential_hash == current_hash:
            return credentials, rotation
        if rotation.next_refresh_credential == credentials.refresh_credential:
            rotation_path.unlink()
        else:
            raise RuntimeError("Pending credential rotation does not match this identity")

    rotation = PendingCredentialRotation(
        base_refresh_credential_hash=_secret_hash(credentials.refresh_credential),
        next_refresh_credential=secret_generator(),
    )
    if not rotation.next_refresh_credential:
        raise ValueError("next refresh credential must be non-empty")
    if rotation.next_refresh_credential == credentials.refresh_credential:
        raise ValueError("next refresh credential must differ from the current credential")
    try:
        _create_private_json(
            rotation_path,
            {
                "formatVersion": 1,
                "baseRefreshCredentialHash": rotation.base_refresh_credential_hash,
                "nextRefreshCredential": rotation.next_refresh_credential,
            },
        )
    except CredentialFileExistsError:
        return prepare_refresh_rotation(
            path,
            secret_generator=secret_generator,
        )
    return credentials, rotation


def commit_refresh_rotation(
    credentials_path: Path,
    rotation: PendingCredentialRotation,
) -> ConnectorCredentials:
    """Promote a server-accepted successor and clear its recovery record."""

    path = credentials_path.expanduser()
    credentials = read_credentials(path)
    if credentials.refresh_credential == rotation.next_refresh_credential:
        _rotation_path(path).unlink(missing_ok=True)
        return credentials
    if _secret_hash(credentials.refresh_credential) != rotation.base_refresh_credential_hash:
        raise RuntimeError("Credential changed while refresh rotation was in progress")
    rotated = ConnectorCredentials(
        server_origin=credentials.server_origin,
        connector_id=credentials.connector_id,
        connector_instance_id=credentials.connector_instance_id,
        refresh_credential=rotation.next_refresh_credential,
    )
    _replace_private_json(path, _credentials_payload(rotated))
    _rotation_path(path).unlink(missing_ok=True)
    return rotated
