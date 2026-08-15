from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dbslice_ai_connector.__main__ import _parse_args, main
from dbslice_ai_connector.sample_data import SampleDataError, download_sample


def sample_archive(*, unsafe_name: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "sample/config/config.json",
            '{"dataset":{"title":"Sample"},"metaData":{"config":{}},"extracts":[]}',
        )
        archive.writestr(
            unsafe_name or "sample/data/metadata/items.json",
            '{"items":[]}',
        )
    return output.getvalue()


def response(payload: bytes):
    return io.BytesIO(payload)


class SampleDataDownloadTest(unittest.TestCase):
    def test_run_accepts_one_dataset_path(self) -> None:
        args = _parse_args(["run", "--dataset", "/datasets/pilot"])
        self.assertEqual(args.dataset, Path("/datasets/pilot"))
        self.assertIsNone(args.dataset_id)

    def test_download_verifies_and_extracts_the_dataset(self) -> None:
        payload = sample_archive()
        requests = []

        def open_request(request, timeout):
            requests.append((request, timeout))
            return response(payload)

        with tempfile.TemporaryDirectory() as directory:
            result = download_sample(
                Path(directory),
                url="https://downloads.example/sample.zip",
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_root="sample",
                open_request=open_request,
            )
            self.assertEqual(result, Path(directory).resolve() / "sample")
            self.assertTrue((result / "config" / "config.json").is_file())

        self.assertEqual(
            requests[0][0].full_url,
            "https://downloads.example/sample.zip",
        )
        self.assertEqual(requests[0][1], 120)

    def test_download_rejects_a_checksum_mismatch_without_installing(self) -> None:
        payload = sample_archive()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            with self.assertRaisesRegex(SampleDataError, "checksum"):
                download_sample(
                    destination,
                    expected_sha256="0" * 64,
                    expected_root="sample",
                    open_request=lambda _request, timeout: response(payload),
                )
            self.assertFalse((destination / "sample").exists())

    def test_download_rejects_unsafe_archive_paths(self) -> None:
        payload = sample_archive(unsafe_name="sample/../../outside.txt")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SampleDataError, "unsafe path"):
                download_sample(
                    Path(directory),
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                    expected_root="sample",
                    open_request=lambda _request, timeout: response(payload),
                )

    def test_download_refuses_to_replace_an_existing_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "sample"
            target.mkdir()
            called = False

            def open_request(_request, _timeout):
                nonlocal called
                called = True
                return response(b"")

            with self.assertRaisesRegex(SampleDataError, "already exists"):
                download_sample(
                    Path(directory),
                    expected_root="sample",
                    open_request=open_request,
                )
            self.assertFalse(called)

    def test_cli_prints_the_normal_run_command(self) -> None:
        stdout = io.StringIO()
        path = Path("/tmp/datasets/dbslice-ai-sample-data-1.0.0")
        with patch(
            "dbslice_ai_connector.__main__.download_sample",
            return_value=path,
        ) as download:
            main(
                ["download-sample", "--destination", "/tmp/datasets"],
                stdout=stdout,
            )

        download.assert_called_once_with(Path("/tmp/datasets"))
        self.assertIn("Downloaded and verified", stdout.getvalue())
        self.assertIn(
            "dbslice-ai-connector run --dataset " + str(path),
            stdout.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
