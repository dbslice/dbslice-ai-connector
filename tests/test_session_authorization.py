from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dbslice_ai_connector.credentials import (
    ConnectorCredentials,
    read_credentials,
    write_new_credentials,
)
from dbslice_ai_connector.session_authorization import (
    ConnectorSessionAuthorization,
    authorize_connector_session,
    exchange_connector_session,
)


class _Response:
    def __init__(self, value: object) -> None:
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class ConnectorSessionAuthorizationTest(unittest.TestCase):
    def test_exchange_posts_rotation_and_derives_websocket_url(self) -> None:
        calls = []

        def open_request(request, timeout):
            calls.append((request, timeout))
            return _Response(
                {
                    "sessionToken": "cst_private",
                }
            )

        result = exchange_connector_session(
            server_origin="https://app.ai.dbslice.org",
            connector_id="ctr_pilot",
            connector_instance_id="ci_pilot",
            refresh_credential="dbr_current",
            next_refresh_credential_hash="a" * 64,
            open_request=open_request,
        )

        self.assertEqual(result.websocket_url, "wss://app.ai.dbslice.org/connector/v1")
        request, timeout = calls[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(
            request.full_url,
            "https://app.ai.dbslice.org/api/connectors/session",
        )
        self.assertEqual(
            json.loads(request.data),
            {
                "connectorId": "ctr_pilot",
                "connectorInstanceId": "ci_pilot",
                "refreshCredential": "dbr_current",
                "nextRefreshCredentialHash": "a" * 64,
            },
        )

    def test_lost_response_resumes_the_same_rotation_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            write_new_credentials(
                path,
                ConnectorCredentials(
                    server_origin="https://app.ai.dbslice.org",
                    connector_id="ctr_pilot",
                    connector_instance_id="ci_pilot",
                    refresh_credential="dbr_current",
                ),
            )
            attempts = []

            def exchange(**values):
                attempts.append(values)
                if len(attempts) == 1:
                    raise OSError("response lost")
                return ConnectorSessionAuthorization(
                    websocket_url="wss://app.ai.dbslice.org/connector/v1",
                    connector_instance_id="ci_pilot",
                    session_token="cst_recovered",
                )

            with self.assertRaises(OSError):
                authorize_connector_session(path, exchange=exchange)
            authorization = authorize_connector_session(path, exchange=exchange)

            self.assertEqual(
                attempts[0]["next_refresh_credential_hash"],
                attempts[1]["next_refresh_credential_hash"],
            )
            self.assertEqual(authorization.session_token, "cst_recovered")
            self.assertNotEqual(
                read_credentials(path).refresh_credential,
                "dbr_current",
            )
            self.assertFalse(path.with_name(".credentials.json.rotation").exists())


if __name__ == "__main__":
    unittest.main()
