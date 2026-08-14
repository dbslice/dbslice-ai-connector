"""Command-line entry point for the dbsliceAI connector."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import signal
import ssl
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO
from urllib.error import URLError

from .client import ConnectorClient
from .credentials import (
    ConnectorCredentials,
    default_credentials_path,
    prepare_new_credentials_path,
    write_new_credentials,
)
from .dataset_provider import DatasetDefinition, FilesystemDatasetProvider
from .enrollment import enroll_connector, generate_connector_instance_id
from .session_authorization import authorize_connector_session_async


def _parse_dataset(value: str) -> DatasetDefinition:
    try:
        alias, display_name, root = value.split("=", 2)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "dataset must use ALIAS=DISPLAY_NAME=PATH"
        ) from error
    return DatasetDefinition(
        alias=alias,
        display_name=display_name,
        root=Path(root),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    enroll = commands.add_parser(
        "enroll",
        help="consume a one-time product enrollment",
    )
    enroll.add_argument(
        "--server-url",
        required=True,
        help="hosted product origin, for example https://app.ai.dbslice.org",
    )
    enroll.add_argument("--connector-instance-id")
    enroll.add_argument("--credentials-file", type=Path)

    run = commands.add_parser("run", help="run the persistent connector")
    run.add_argument("--server-url")
    run.add_argument("--connector-instance-id")
    run.add_argument("--credentials-file", type=Path)
    run.add_argument(
        "--dataset",
        action="append",
        type=_parse_dataset,
        required=True,
        help="ALIAS=DISPLAY_NAME=PATH; repeat for multiple aliases",
    )
    run.add_argument("--reconnect-initial-ms", type=int, default=100)
    run.add_argument("--reconnect-max-ms", type=int, default=5000)
    return parser


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] not in {"enroll", "run", "-h", "--help"}:
        values.insert(0, "run")
    return _parser().parse_args(values)


def _enrollment_token(
    environ: Mapping[str, str],
    secret_reader: Callable[[str], str],
) -> str:
    token = environ.get("DBSLICE_CONNECTOR_ENROLLMENT_TOKEN")
    if token is None:
        token = secret_reader("Enrollment token: ")
    if not token:
        raise ValueError("Enrollment token must be non-empty")
    return token


def _enroll(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str],
    stdout: TextIO,
    secret_reader: Callable[[str], str],
) -> None:
    credential_path = prepare_new_credentials_path(
        args.credentials_file or default_credentials_path(environ)
    )
    instance_id = args.connector_instance_id or generate_connector_instance_id()
    result = enroll_connector(
        server_url=args.server_url,
        enrollment_token=_enrollment_token(environ, secret_reader),
        connector_instance_id=instance_id,
    )
    write_new_credentials(
        credential_path,
        ConnectorCredentials(
            server_origin=result.server_origin,
            connector_id=result.connector_id,
            connector_instance_id=result.connector_instance_id,
            refresh_credential=result.refresh_credential,
        ),
    )
    stdout.write(
        json.dumps(
            {
                "event": "enrolled",
                "connectorId": result.connector_id,
                "connectorInstanceId": result.connector_instance_id,
                "credentialsFile": str(credential_path.expanduser()),
            },
            separators=(",", ":"),
        )
        + "\n"
    )


async def _run(args: argparse.Namespace, *, environ: Mapping[str, str]) -> None:
    provider = FilesystemDatasetProvider(args.dataset)
    development_credential = environ.get("DBSLICE_CONNECTOR_CREDENTIAL")
    if development_credential:
        if args.credentials_file:
            raise ValueError(
                "--credentials-file cannot be used with "
                "DBSLICE_CONNECTOR_CREDENTIAL"
            )
        if not args.server_url or not args.connector_instance_id:
            raise ValueError(
                "--server-url and --connector-instance-id are required with "
                "DBSLICE_CONNECTOR_CREDENTIAL"
            )
        connection = {
            "server_url": args.server_url,
            "connector_instance_id": args.connector_instance_id,
            "credential": development_credential,
        }
    else:
        if args.server_url or args.connector_instance_id:
            raise ValueError(
                "product connector identity comes from --credentials-file; "
                "do not supply --server-url or --connector-instance-id"
            )
        credentials_path = args.credentials_file or default_credentials_path(environ)
        connection = {
            "authorization_provider": lambda: authorize_connector_session_async(
                credentials_path
            )
        }

    client = ConnectorClient(
        provider=provider,
        **connection,
        reconnect_initial_ms=args.reconnect_initial_ms,
        reconnect_max_ms=args.reconnect_max_ms,
        event_sink=lambda event: print(
            json.dumps(event, separators=(",", ":")),
            flush=True,
        ),
    )

    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            signal_name,
            lambda: asyncio.create_task(client.stop()),
        )
    await client.run()


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    secret_reader: Callable[[str], str] = getpass.getpass,
) -> None:
    args = _parse_args(argv)
    if args.command == "enroll":
        _enroll(
            args,
            environ=environ,
            stdout=stdout,
            secret_reader=secret_reader,
        )
        return
    asyncio.run(_run(args, environ=environ))


def cli(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    secret_reader: Callable[[str], str] = getpass.getpass,
) -> int:
    """Run the command with concise, actionable operator-facing failures."""

    try:
        main(
            argv,
            environ=environ,
            stdout=stdout,
            secret_reader=secret_reader,
        )
    except URLError as error:
        reason = error.reason
        if (
            isinstance(reason, ssl.SSLCertVerificationError)
            or "CERTIFICATE_VERIFY_FAILED" in str(reason)
        ):
            stderr.write(
                "Could not verify the server TLS certificate.\n"
                "If you installed Python from python.org on macOS, run its "
                "bundled Install Certificates.command, then retry.\n"
                "Certificate verification was not disabled.\n"
            )
        else:
            stderr.write(f"Could not connect to the hosted service: {reason}\n")
        return 1
    except (PermissionError, RuntimeError, ValueError) as error:
        stderr.write(f"Error: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
