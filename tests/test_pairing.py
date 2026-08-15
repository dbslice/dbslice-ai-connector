from __future__ import annotations

import io
import json
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from dbslice_ai_connector.__main__ import cli, main
from dbslice_ai_connector.credentials import read_credentials
from dbslice_ai_connector.pairing import (
    ConnectorPairingError,
    PairingResult,
    PendingPairing,
    poll_pairing,
    start_pairing,
    wait_for_pairing,
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


def _pending_pairing() -> PendingPairing:
    return PendingPairing(
        server_origin="https://app.ai.dbslice.org",
        connector_instance_id="ci_pilot",
        refresh_credential="dbr_private",
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://app.ai.dbslice.org/connectors/pair",
        verification_uri_complete=(
            "https://app.ai.dbslice.org/connectors/pair#code=ABCD-EFGH"
        ),
        expires_at="2026-08-15T12:10:00.000Z",
        interval_seconds=2,
    )


class ConnectorPairingTest(unittest.TestCase):
    def test_start_posts_only_a_refresh_hash(self) -> None:
        calls = []

        def open_request(request, timeout):
            calls.append((request, timeout))
            return _Response(
                {
                    "deviceCode": "device-secret",
                    "userCode": "ABCD-EFGH",
                    "verificationUri": (
                        "https://app.ai.dbslice.org/connectors/pair"
                    ),
                    "verificationUriComplete": (
                        "https://app.ai.dbslice.org/connectors/pair#code=ABCD-EFGH"
                    ),
                    "expiresAt": "2026-08-15T12:10:00.000Z",
                    "intervalSeconds": 2,
                }
            )

        result = start_pairing(
            server_url="https://app.ai.dbslice.org",
            connector_instance_id="ci_pilot",
            display_name="Pilot laptop",
            refresh_credential="dbr_private",
            open_request=open_request,
        )

        self.assertEqual(result.user_code, "ABCD-EFGH")
        request, timeout = calls[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(
            request.full_url,
            "https://app.ai.dbslice.org/api/connectors/pairings",
        )
        payload = json.loads(request.data)
        self.assertEqual(payload["connectorInstanceId"], "ci_pilot")
        self.assertEqual(payload["displayName"], "Pilot laptop")
        self.assertEqual(len(payload["refreshCredentialHash"]), 64)
        self.assertNotIn("dbr_private", request.data.decode("utf-8"))

    def test_rejects_remote_plain_http_and_cross_origin_browser_url(self) -> None:
        with self.assertRaises(ValueError):
            start_pairing(
                server_url="http://example.test",
                connector_instance_id="ci_pilot",
                display_name="Pilot laptop",
            )
        with self.assertRaises(ConnectorPairingError):
            start_pairing(
                server_url="http://127.0.0.1:3001",
                connector_instance_id="ci_pilot",
                display_name="Pilot laptop",
                open_request=lambda _request, _timeout: _Response(
                    {
                        "deviceCode": "device-secret",
                        "userCode": "ABCD-EFGH",
                        "verificationUri": "https://attacker.example/pair",
                        "verificationUriComplete": "https://attacker.example/pair#code=x",
                        "expiresAt": "2026-08-15T12:10:00.000Z",
                        "intervalSeconds": 2,
                    }
                ),
            )

    def test_polling_waits_then_returns_the_client_held_credential(self) -> None:
        pairing = _pending_pairing()
        responses = iter(
            [
                {"status": "pending", "intervalSeconds": 2},
                {
                    "connectorId": "ctr_pilot",
                    "connectorInstanceId": "ci_pilot",
                    "status": "enrolled",
                    "enrolledAt": "2026-08-15T12:01:00.000Z",
                },
            ]
        )
        sleeps = []
        result = wait_for_pairing(
            pairing,
            open_request=lambda _request, _timeout: _Response(next(responses)),
            sleep=sleeps.append,
        )
        self.assertEqual(sleeps, [2])
        self.assertEqual(result.refresh_credential, "dbr_private")
        self.assertEqual(result.connector_id, "ctr_pilot")

    def test_poll_rejects_a_mismatched_installation(self) -> None:
        with self.assertRaises(ConnectorPairingError):
            poll_pairing(
                _pending_pairing(),
                open_request=lambda _request, _timeout: _Response(
                    {
                        "connectorId": "ctr_pilot",
                        "connectorInstanceId": "ci_other",
                        "status": "enrolled",
                        "enrolledAt": "2026-08-15T12:01:00.000Z",
                    }
                ),
            )

    def test_cli_opens_browser_and_writes_only_safe_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            stdout = io.StringIO()
            pairing = _pending_pairing()
            result = PairingResult(
                connector_id="ctr_pilot",
                connector_instance_id="ci_pilot",
                refresh_credential="dbr_private",
                status="enrolled",
                enrolled_at="2026-08-15T12:01:00.000Z",
                server_origin="https://app.ai.dbslice.org",
            )
            opened = []
            with (
                patch(
                    "dbslice_ai_connector.__main__.generate_connector_instance_id",
                    return_value="ci_pilot",
                ),
                patch(
                    "dbslice_ai_connector.__main__.start_pairing",
                    return_value=pairing,
                ) as start,
                patch(
                    "dbslice_ai_connector.__main__.wait_for_pairing",
                    return_value=result,
                ) as wait,
            ):
                main(
                    [
                        "pair",
                        "--server-url",
                        "https://app.ai.dbslice.org",
                        "--name",
                        "Pilot laptop",
                        "--credentials-file",
                        str(path),
                    ],
                    environ={},
                    stdout=stdout,
                    browser_opener=lambda url: not opened.append(url),
                )

            start.assert_called_once_with(
                server_url="https://app.ai.dbslice.org",
                connector_instance_id="ci_pilot",
                display_name="Pilot laptop",
            )
            wait.assert_called_once_with(pairing)
            self.assertEqual(opened, [pairing.verification_uri_complete])
            self.assertIn('"event":"pairing_started"', stdout.getvalue())
            self.assertIn('"event":"paired"', stdout.getvalue())
            self.assertNotIn("device-secret", stdout.getvalue())
            self.assertNotIn("dbr_private", stdout.getvalue())
            self.assertEqual(read_credentials(path).refresh_credential, "dbr_private")

    def test_cli_refuses_existing_file_before_starting_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text("existing", encoding="utf-8")
            with patch("dbslice_ai_connector.__main__.start_pairing") as start:
                with self.assertRaises(RuntimeError):
                    main(
                        [
                            "pair",
                            "--server-url",
                            "https://app.ai.dbslice.org",
                            "--credentials-file",
                            str(path),
                        ],
                        environ={},
                        stdout=io.StringIO(),
                    )
            start.assert_not_called()

    def test_installed_cli_explains_missing_certificate_authority(self) -> None:
        stderr = io.StringIO()
        tls_error = ssl.SSLCertVerificationError(
            1,
            "certificate verify failed: unable to get local issuer certificate",
        )
        with patch(
            "dbslice_ai_connector.__main__.main",
            side_effect=URLError(tls_error),
        ):
            result = cli([], stderr=stderr)
        self.assertEqual(result, 1)
        self.assertIn("Could not verify the server TLS certificate", stderr.getvalue())
        self.assertIn("Install Certificates.command", stderr.getvalue())
        self.assertIn("was not disabled", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    @unittest.skipUnless(os.name == "posix", "POSIX symlink check")
    def test_cli_rejects_symlinked_directory_before_starting_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir(mode=0o700)
            linked = root / "linked"
            linked.symlink_to(private, target_is_directory=True)
            with patch("dbslice_ai_connector.__main__.start_pairing") as start:
                with self.assertRaises(PermissionError):
                    main(
                        [
                            "pair",
                            "--server-url",
                            "https://app.ai.dbslice.org",
                            "--credentials-file",
                            str(linked / "credentials.json"),
                        ],
                        environ={},
                        stdout=io.StringIO(),
                    )
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
