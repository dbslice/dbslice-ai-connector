from __future__ import annotations

import json
from pathlib import Path
import unittest

from dbslice_ai_connector.client import ConnectorClient
from dbslice_ai_connector.protocol_validation import load_protocol_schema


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_protocol_schema(REPO_ROOT / "protocol" / "v1" / "protocol.schema.json")


class FakeProvider:
    def execute(self, dataset_alias, operation, args):
        return {
            "itemId": args["itemId"],
            "datasetAlias": dataset_alias,
        }


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, encoded):
        self.messages.append(json.loads(encoded))


class ConnectorClientMetricsTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_emits_local_stage_timings_without_changing_wire_message(self):
        events = []
        websocket = FakeWebSocket()
        client = ConnectorClient(
            server_url="ws://example.test/connector/v1",
            connector_instance_id="ci_metricfixture",
            provider=FakeProvider(),
            schema=SCHEMA,
            event_sink=events.append,
        )
        client.websocket = websocket
        request = {
            "protocolVersion": "1",
            "messageType": "operation.request",
            "requestId": "req_metricfixture",
            "operation": "getItemById",
            "datasetAlias": "synthetic-study",
            "args": {"itemId": "case-001"},
        }

        await client._execute_operation(request)

        self.assertEqual(len(websocket.messages), 1)
        self.assertEqual(websocket.messages[0]["messageType"], "operation.success")
        self.assertNotIn("metrics", websocket.messages[0])
        self.assertEqual(len(events), 1)
        metric = events[0]
        self.assertEqual(metric["event"], "operation_metric")
        self.assertEqual(metric["requestId"], "req_metricfixture")
        self.assertEqual(metric["status"], "completed")
        for name in (
            "providerMs",
            "protocolValidationMs",
            "serializationMs",
            "socketSendMs",
            "durationMs",
        ):
            self.assertGreaterEqual(metric[name], 0)
        self.assertGreater(metric["responseBytes"], 0)


if __name__ == "__main__":
    unittest.main()
