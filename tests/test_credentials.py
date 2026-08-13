from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dbslice_ai_connector.credentials import (
    ConnectorCredentials,
    CredentialFileExistsError,
    commit_refresh_rotation,
    default_credentials_path,
    prepare_refresh_rotation,
    read_credentials,
    write_new_credentials,
)


class ConnectorCredentialFileTest(unittest.TestCase):
    def test_private_file_is_created_and_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "credentials.json"
            credentials = ConnectorCredentials(
                server_origin="https://app.ai.dbslice.org",
                connector_id="ctr_pilot",
                connector_instance_id="ci_pilot",
                refresh_credential="dbr_private",
            )

            write_new_credentials(path, credentials)

            self.assertEqual(read_credentials(path), credentials)
            if os.name == "posix":
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(CredentialFileExistsError):
                write_new_credentials(
                    path,
                    ConnectorCredentials(
                        server_origin="https://other.example.test",
                        connector_id="ctr_other",
                        connector_instance_id="ci_other",
                        refresh_credential="dbr_other",
                    ),
                )
            self.assertEqual(read_credentials(path), credentials)

    def test_default_path_honours_xdg_config_home(self) -> None:
        self.assertEqual(
            default_credentials_path({"XDG_CONFIG_HOME": "/private/config"}),
            Path("/private/config/dbslice-ai-connector/credentials.json"),
        )

    def test_refresh_rotation_is_durable_resumable_and_atomic(self) -> None:
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
            credentials, first = prepare_refresh_rotation(
                path,
                secret_generator=lambda: "dbr_next",
            )
            _, resumed = prepare_refresh_rotation(
                path,
                secret_generator=lambda: self.fail("rotation should be resumed"),
            )
            self.assertEqual(credentials.refresh_credential, "dbr_current")
            self.assertEqual(resumed, first)

            rotated = commit_refresh_rotation(path, first)

            self.assertEqual(rotated.refresh_credential, "dbr_next")
            self.assertEqual(read_credentials(path), rotated)
            self.assertFalse(path.with_name(".credentials.json.rotation").exists())
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permission check")
    def test_insecure_credential_file_is_rejected_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            write_new_credentials(
                path,
                ConnectorCredentials(
                    server_origin="https://app.ai.dbslice.org",
                    connector_id="ctr_pilot",
                    connector_instance_id="ci_pilot",
                    refresh_credential="dbr_private",
                ),
            )
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                read_credentials(path)

    @unittest.skipUnless(os.name == "posix", "POSIX permission check")
    def test_existing_shared_directory_is_rejected_without_changing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "shared"
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            with self.assertRaises(PermissionError):
                write_new_credentials(
                    parent / "credentials.json",
                    ConnectorCredentials(
                        server_origin="https://app.ai.dbslice.org",
                        connector_id="ctr_pilot",
                        connector_instance_id="ci_pilot",
                        refresh_credential="dbr_private",
                    ),
                )
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
