"""Command-line entry point for the dbsliceAI connector."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from .client import ConnectorClient
from .dataset_provider import DatasetDefinition, FilesystemDatasetProvider


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
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--connector-instance-id", required=True)
    parser.add_argument(
        "--dataset",
        action="append",
        type=_parse_dataset,
        required=True,
        help="ALIAS=DISPLAY_NAME=PATH; repeat for multiple aliases",
    )
    parser.add_argument("--reconnect-initial-ms", type=int, default=100)
    parser.add_argument("--reconnect-max-ms", type=int, default=5000)
    return parser


async def _run(args: argparse.Namespace) -> None:
    provider = FilesystemDatasetProvider(args.dataset)
    client = ConnectorClient(
        server_url=args.server_url,
        connector_instance_id=args.connector_instance_id,
        provider=provider,
        credential=os.environ.get("DBSLICE_CONNECTOR_CREDENTIAL"),
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


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
