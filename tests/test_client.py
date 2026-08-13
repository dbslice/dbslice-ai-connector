from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dbslice_ai_connector.client import ConnectorClient
from dbslice_ai_connector.protocol_validation import load_protocol_schema


class _Provider:
    def advertisements(self):
        return [{"datasetAlias": "pilot", "displayName": "Pilot dataset"}]


class _WebSocket:
    def __init__(self) -> None:
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        return json.dumps(
            {
                "protocolVersion": "1",
                "messageType": "session.accepted",
                "sessionId": "cs_pilot0001",
                "heartbeatIntervalMs": 15000,
                "requestTimeoutMs": 30000,
                "maxInFlightRequests": 8,
                "maxMessageBytes": 25165824,
            }
        )

    def __aiter__(self):
        async def messages():
            if False:
                yield None

        return messages()


class ConnectorClientAuthorizationTest(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_provider_supplies_each_websocket_handshake(self) -> None:
        websocket = _WebSocket()
        calls = []

        def connect(url, **options):
            calls.append((url, options))
            return websocket

        async def authorize():
            return SimpleNamespace(
                websocket_url="wss://app.ai.dbslice.org/connector/v1",
                connector_instance_id="ci_pilot0001",
                session_token="cst_private",
            )

        client = ConnectorClient(
            provider=_Provider(),
            authorization_provider=authorize,
            schema=load_protocol_schema(Path("protocol/v1/protocol.schema.json")),
        )
        with patch("dbslice_ai_connector.client.connect", connect):
            await client._run_session()

        self.assertEqual(calls[0][0], "wss://app.ai.dbslice.org/connector/v1")
        self.assertEqual(
            calls[0][1]["additional_headers"],
            {"Authorization": "Bearer cst_private"},
        )
        self.assertEqual(websocket.sent[0]["connectorInstanceId"], "ci_pilot0001")
        self.assertEqual(websocket.sent[1]["messageType"], "dataset.advertise")


if __name__ == "__main__":
    unittest.main()
