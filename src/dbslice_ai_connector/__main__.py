"""Command-line entry point for the dbsliceAI connector."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import signal
import socket
import ssl
import sys
import webbrowser
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
from .dataset_provider import (
    FilesystemDatasetProvider,
    dataset_definition_from_root,
)
from .pairing import (
    generate_connector_instance_id,
    start_pairing,
    wait_for_pairing,
)
from .session_authorization import authorize_connector_session_async
from .sample_data import download_sample


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    pair = commands.add_parser(
        "pair",
        help="connect this installation through browser sign-in",
    )
    pair.add_argument(
        "--server-url",
        required=True,
        help="hosted product origin, for example https://app.ai.dbslice.org",
    )
    pair.add_argument("--name", default=socket.gethostname())
    pair.add_argument("--connector-instance-id")
    pair.add_argument("--credentials-file", type=Path)
    pair.add_argument("--no-open-browser", action="store_true")

    sample = commands.add_parser(
        "download-sample",
        help="download and verify the dbsliceAI sample dataset",
    )
    sample.add_argument(
        "--destination",
        type=Path,
        default=Path.cwd(),
        help=(
            "directory in which to create the sample dataset "
            "(default: current directory)"
        ),
    )

    run = commands.add_parser("run", help="run the persistent connector")
    run.add_argument("--server-url")
    run.add_argument("--connector-instance-id")
    run.add_argument("--credentials-file", type=Path)
    run.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="path to one dbsliceAI dataset",
    )
    run.add_argument(
        "--dataset-id",
        help="override the ID derived from the dataset title",
    )
    run.add_argument("--reconnect-initial-ms", type=int, default=100)
    run.add_argument("--reconnect-max-ms", type=int, default=5000)
    return parser


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] not in {
        "pair",
        "download-sample",
        "run",
        "-h",
        "--help",
    }:
        values.insert(0, "run")
    return _parser().parse_args(values)


def _pair(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str],
    stdout: TextIO,
    browser_opener: Callable[[str], bool],
) -> None:
    credential_path = prepare_new_credentials_path(
        args.credentials_file or default_credentials_path(environ)
    )
    instance_id = args.connector_instance_id or generate_connector_instance_id()
    pairing = start_pairing(
        server_url=args.server_url,
        connector_instance_id=instance_id,
        display_name=args.name,
    )
    stdout.write(
        json.dumps(
            {
                "event": "pairing_started",
                "verificationUrl": pairing.verification_uri_complete,
                "userCode": pairing.user_code,
                "expiresAt": pairing.expires_at,
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    stdout.flush()
    if not args.no_open_browser:
        try:
            browser_opener(pairing.verification_uri_complete)
        except webbrowser.Error:
            pass
    result = wait_for_pairing(pairing)
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
                "event": "paired",
                "connectorId": result.connector_id,
                "connectorInstanceId": result.connector_instance_id,
                "credentialsFile": str(credential_path.expanduser()),
            },
            separators=(",", ":"),
        )
        + "\n"
    )


async def _run(args: argparse.Namespace, *, environ: Mapping[str, str]) -> None:
    provider = FilesystemDatasetProvider(
        [
            dataset_definition_from_root(
                args.dataset,
                dataset_id=args.dataset_id,
            )
        ]
    )
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
    browser_opener: Callable[[str], bool] = webbrowser.open,
) -> None:
    args = _parse_args(argv)
    if args.command == "pair":
        _pair(
            args,
            environ=environ,
            stdout=stdout,
            browser_opener=browser_opener,
        )
        return
    if args.command == "download-sample":
        dataset_path = download_sample(args.destination)
        stdout.write(
            "Downloaded and verified the sample dataset:\n\n"
            f"  {dataset_path}\n\n"
            "Add it to the connector with:\n\n"
            "  dbslice-ai-connector run --dataset "
            f"{shlex.quote(str(dataset_path))}\n"
        )
        return
    asyncio.run(_run(args, environ=environ))


def cli(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    browser_opener: Callable[[str], bool] = webbrowser.open,
) -> int:
    """Run the command with concise, actionable operator-facing failures."""

    try:
        main(
            argv,
            environ=environ,
            stdout=stdout,
            browser_opener=browser_opener,
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
