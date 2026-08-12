from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbslice_ai_connector.__main__ import main
from dbslice_ai_connector.credentials import read_credentials
from dbslice_ai_connector.enrollment import (
    ConnectorEnrollmentError,
    EnrollmentResult,
    enroll_connector,
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


class ConnectorEnrollmentTest(unittest.TestCase):
    def test_posts_secret_and_validates_bound_instance(self) -> None:
        calls = []

        def open_request(request, timeout):
            calls.append((request, timeout))
            return _Response(
                {
                    "connectorId": "ctr_pilot",
                    "connectorInstanceId": "ci_pilot",
                    "refreshCredential": "dbr_private",
                    "status": "enrolled",
                    "enrolledAt": "2026-08-12T12:00:00.000Z",
                }
            )

        result = enroll_connector(
            server_url="https://app.ai.dbslice.org",
            enrollment_token="one-time-secret",
            connector_instance_id="ci_pilot",
            open_request=open_request,
        )

        self.assertEqual(result.connector_id, "ctr_pilot")
        request, timeout = calls[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(
            request.full_url,
            "https://app.ai.dbslice.org/api/connectors/enroll",
        )
        self.assertEqual(
            json.loads(request.data),
            {
                "enrollmentToken": "one-time-secret",
                "connectorInstanceId": "ci_pilot",
            },
        )

    def test_rejects_remote_plain_http_and_wrong_instance(self) -> None:
        with self.assertRaises(ValueError):
            enroll_connector(
                server_url="http://example.test",
                enrollment_token="secret",
                connector_instance_id="ci_pilot",
            )
        with self.assertRaises(ConnectorEnrollmentError):
            enroll_connector(
                server_url="http://127.0.0.1:3001",
                enrollment_token="secret",
                connector_instance_id="ci_pilot",
                open_request=lambda _request, _timeout: _Response(
                    {
                        "connectorId": "ctr_pilot",
                        "connectorInstanceId": "ci_other",
                        "refreshCredential": "dbr_private",
                        "status": "enrolled",
                        "enrolledAt": "2026-08-12T12:00:00.000Z",
                    }
                ),
            )

    def test_cli_prompts_for_token_and_writes_only_safe_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            stdout = io.StringIO()
            result = EnrollmentResult(
                connector_id="ctr_pilot",
                connector_instance_id="ci_generated",
                refresh_credential="dbr_private",
                status="enrolled",
                enrolled_at="2026-08-12T12:00:00.000Z",
                server_origin="https://app.ai.dbslice.org",
            )
            with (
                patch(
                    "dbslice_ai_connector.__main__.generate_connector_instance_id",
                    return_value="ci_generated",
                ),
                patch(
                    "dbslice_ai_connector.__main__.enroll_connector",
                    return_value=result,
                ) as enroll,
            ):
                main(
                    [
                        "enroll",
                        "--server-url",
                        "https://app.ai.dbslice.org",
                        "--credentials-file",
                        str(path),
                    ],
                    environ={},
                    stdout=stdout,
                    secret_reader=lambda _prompt: "one-time-secret",
                )

            enroll.assert_called_once_with(
                server_url="https://app.ai.dbslice.org",
                enrollment_token="one-time-secret",
                connector_instance_id="ci_generated",
            )
            self.assertNotIn("one-time-secret", stdout.getvalue())
            self.assertNotIn("dbr_private", stdout.getvalue())
            self.assertEqual(
                read_credentials(path).refresh_credential,
                "dbr_private",
            )

    def test_cli_refuses_existing_file_before_consuming_enrollment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text("existing", encoding="utf-8")
            with patch("dbslice_ai_connector.__main__.enroll_connector") as enroll:
                with self.assertRaises(RuntimeError):
                    main(
                        [
                            "enroll",
                            "--server-url",
                            "https://app.ai.dbslice.org",
                            "--credentials-file",
                            str(path),
                        ],
                        environ={"DBSLICE_CONNECTOR_ENROLLMENT_TOKEN": "secret"},
                        stdout=io.StringIO(),
                        secret_reader=lambda _prompt: self.fail("unexpected prompt"),
                    )
            enroll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
