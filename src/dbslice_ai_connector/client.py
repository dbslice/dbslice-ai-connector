"""Persistent outbound WebSocket client for connector protocol V1."""

from __future__ import annotations

import asyncio
import json
import random
import sysconfig
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from websockets.asyncio.client import connect

from . import __version__
from .dataset_provider import DatasetOperationError, FilesystemDatasetProvider
from .protocol_validation import load_protocol_schema, validate_protocol_message

PROTOCOL_VERSION = "1"
MAX_MESSAGE_BYTES = 24 * 1024 * 1024


def _default_schema() -> dict[str, Any]:
    candidates = [
        (
            Path(__file__).resolve().parents[2]
            / "protocol"
            / "v1"
            / "protocol.schema.json"
        ),
        (
            Path(sysconfig.get_path("data"))
            / "share"
            / "dbslice-ai-connector"
            / "protocol"
            / "v1"
            / "protocol.schema.json"
        ),
    ]
    for schema_path in candidates:
        if schema_path.is_file():
            return load_protocol_schema(schema_path)
    raise RuntimeError(
        "Connector protocol schema was not found; pass an explicit schema "
        "when embedding ConnectorClient"
    )


class ConnectorClient:
    """Maintain one reconnecting connector session until explicitly stopped."""

    def __init__(
        self,
        *,
        server_url: str,
        connector_instance_id: str,
        provider: FilesystemDatasetProvider,
        credential: str | None = None,
        schema: dict[str, Any] | None = None,
        reconnect_initial_ms: int = 100,
        reconnect_max_ms: int = 5000,
        handshake_timeout_ms: int = 5000,
        operation_delay_ms: int = 0,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.server_url = server_url
        self.connector_instance_id = connector_instance_id
        self.provider = provider
        self.credential = credential
        self.schema = schema or _default_schema()
        self.reconnect_initial_ms = reconnect_initial_ms
        self.reconnect_max_ms = reconnect_max_ms
        self.handshake_timeout_ms = handshake_timeout_ms
        self.operation_delay_ms = operation_delay_ms
        self.event_sink = event_sink or (lambda _event: None)
        self.stop_event = asyncio.Event()
        self.websocket = None
        self.send_lock = asyncio.Lock()
        self.operation_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_request_ids: deque[str] = deque(maxlen=1000)
        self.max_in_flight_requests = 1
        self.semaphore = asyncio.Semaphore(1)

    async def run(self) -> None:
        delay_ms = self.reconnect_initial_ms
        while not self.stop_event.is_set():
            try:
                await self._run_session()
                delay_ms = self.reconnect_initial_ms
            except asyncio.CancelledError:
                raise
            except Exception as error:  # reconnect boundary
                self._emit("disconnected", error=str(error))

            if self.stop_event.is_set():
                break
            jitter = random.uniform(0.8, 1.2)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=(delay_ms * jitter) / 1000,
                )
            except TimeoutError:
                pass
            delay_ms = min(delay_ms * 2, self.reconnect_max_ms)

    async def stop(self) -> None:
        self.stop_event.set()
        if self.websocket is not None:
            await self.websocket.close(code=1000, reason="Connector stopping")
        await self._cancel_operations()

    async def _run_session(self) -> None:
        async with connect(
            self.server_url,
            max_size=MAX_MESSAGE_BYTES,
            ping_interval=None,
            compression=None,
            additional_headers=(
                {"Authorization": f"Bearer {self.credential}"}
                if self.credential
                else None
            ),
        ) as websocket:
            self.websocket = websocket
            try:
                await self._send(
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "messageType": "session.hello",
                        "connectorVersion": __version__,
                        "connectorInstanceId": self.connector_instance_id,
                        "supportedProtocolVersions": [PROTOCOL_VERSION],
                        "capabilities": [
                            "dataset.advertise",
                            "heartbeat",
                            "operation.cancel",
                        ],
                    }
                )
                accepted = await asyncio.wait_for(
                    self._receive(),
                    timeout=self.handshake_timeout_ms / 1000,
                )
                if accepted.get("messageType") != "session.accepted":
                    raise RuntimeError("Expected session.accepted from server")
                self.max_in_flight_requests = accepted["maxInFlightRequests"]
                self.semaphore = asyncio.Semaphore(self.max_in_flight_requests)
                await self._send(
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "messageType": "dataset.advertise",
                        "datasets": self.provider.advertisements(),
                    }
                )
                self._emit("connected", sessionId=accepted["sessionId"])

                async for raw_message in websocket:
                    if not isinstance(raw_message, str):
                        raise RuntimeError(
                            "WebSocket binary frames are not valid in connector protocol V1"
                        )
                    message = json.loads(raw_message)
                    validate_protocol_message(message, schema=self.schema)
                    await self._handle_message(message)
            finally:
                self.websocket = None
                await self._cancel_operations()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message["messageType"]
        if message_type == "heartbeat.ping":
            await self._send(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "messageType": "heartbeat.pong",
                    "heartbeatId": message["heartbeatId"],
                }
            )
            self._emit("heartbeat", heartbeatId=message["heartbeatId"])
            return
        if message_type == "heartbeat.pong":
            return
        if message_type == "operation.cancel":
            await self._cancel_operation(message)
            return
        if message_type != "operation.request":
            raise RuntimeError(f"Unexpected server message type: {message_type}")

        request_id = message["requestId"]
        if (
            request_id in self.operation_tasks
            or request_id in self.completed_request_ids
        ):
            await self._send_operation_error(
                message,
                DatasetOperationError(
                    "INVALID_REQUEST",
                    f"Duplicate request ID: {request_id}",
                ),
            )
            return
        if len(self.operation_tasks) >= self.max_in_flight_requests:
            await self._send_operation_error(
                message,
                DatasetOperationError(
                    "UNAVAILABLE",
                    "Connector has reached its in-flight request limit",
                    retryable=True,
                ),
            )
            return

        task = asyncio.create_task(self._execute_operation(message))
        self.operation_tasks[request_id] = task
        task.add_done_callback(
            lambda completed, identifier=request_id: self._operation_done(
                identifier, completed
            )
        )

    async def _execute_operation(self, request: dict[str, Any]) -> None:
        try:
            async with self.semaphore:
                if self.operation_delay_ms:
                    await asyncio.sleep(self.operation_delay_ms / 1000)
                result = await asyncio.to_thread(
                    self.provider.execute,
                    request["datasetAlias"],
                    request["operation"],
                    request["args"],
                )
                await self._send(
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "messageType": "operation.success",
                        "requestId": request["requestId"],
                        "operation": request["operation"],
                        "ok": True,
                        "result": result,
                    }
                )
        except asyncio.CancelledError:
            raise
        except DatasetOperationError as error:
            await self._send_operation_error(request, error)
        except Exception:
            await self._send_operation_error(
                request,
                DatasetOperationError(
                    "INTERNAL",
                    "Connector failed to complete the dataset operation",
                    retryable=False,
                ),
            )

    async def _send_operation_error(
        self,
        request: dict[str, Any],
        error: DatasetOperationError,
    ) -> None:
        wire_error: dict[str, Any] = {
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
        }
        if error.details is not None:
            wire_error["details"] = error.details
        await self._send(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "messageType": "operation.error",
                "requestId": request["requestId"],
                "operation": request["operation"],
                "ok": False,
                "error": wire_error,
            }
        )

    async def _cancel_operation(self, message: dict[str, Any]) -> None:
        request_id = message["requestId"]
        task = self.operation_tasks.get(request_id)
        if task is None:
            status = (
                "already_completed"
                if request_id in self.completed_request_ids
                else "not_found"
            )
        elif task.done():
            status = "already_completed"
        else:
            task.cancel()
            status = "accepted"
        await self._send(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "messageType": "operation.cancelled",
                "requestId": request_id,
                "status": status,
            }
        )

    def _operation_done(
        self,
        request_id: str,
        task: asyncio.Task[None],
    ) -> None:
        self.operation_tasks.pop(request_id, None)
        self.completed_request_ids.append(request_id)
        with suppress(asyncio.CancelledError):
            task.exception()

    async def _cancel_operations(self) -> None:
        tasks = list(self.operation_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.operation_tasks.clear()

    async def _send(self, message: dict[str, Any]) -> None:
        validate_protocol_message(message, schema=self.schema)
        if self.websocket is None:
            raise RuntimeError("Connector WebSocket is not connected")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise DatasetOperationError(
                "TOO_LARGE",
                "Connector message exceeds the protocol frame limit",
            )
        async with self.send_lock:
            await self.websocket.send(encoded)

    async def _receive(self) -> dict[str, Any]:
        if self.websocket is None:
            raise RuntimeError("Connector WebSocket is not connected")
        raw_message = await self.websocket.recv()
        if not isinstance(raw_message, str):
            raise RuntimeError(
                "WebSocket binary frames are not valid in connector protocol V1"
            )
        message = json.loads(raw_message)
        validate_protocol_message(message, schema=self.schema)
        return message

    def _emit(self, event: str, **details: Any) -> None:
        self.event_sink({"event": event, **details})
