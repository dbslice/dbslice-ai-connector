"""Download and verify the public dbsliceAI sample dataset."""

from __future__ import annotations

import hashlib
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable
from urllib.request import Request, urlopen


SAMPLE_VERSION = "1.0.0"
SAMPLE_ROOT_NAME = f"dbslice-ai-sample-data-{SAMPLE_VERSION}"
SAMPLE_ARCHIVE_NAME = f"{SAMPLE_ROOT_NAME}.zip"
SAMPLE_DOWNLOAD_URL = (
    "https://github.com/dbslice/dbslice-ai-sample-data/releases/download/"
    f"v{SAMPLE_VERSION}/{SAMPLE_ARCHIVE_NAME}"
)
SAMPLE_ARCHIVE_SHA256 = (
    "7a02f24beb78634ce1f32bbb3c91cac7a4af7bbdf3b04223b8734ec85f366871"
)
MAX_ARCHIVE_FILES = 10_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class SampleDataError(RuntimeError):
    """Raised when the sample cannot be downloaded or safely unpacked."""


def _validate_archive(archive: zipfile.ZipFile, expected_root: str) -> None:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_FILES:
        raise SampleDataError("sample archive contains too many files")
    if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
        raise SampleDataError("sample archive is unexpectedly large")

    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        mode = member.external_attr >> 16
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != expected_root
            or stat.S_ISLNK(mode)
        ):
            raise SampleDataError("sample archive contains an unsafe path")


def download_sample(
    destination: Path,
    *,
    url: str = SAMPLE_DOWNLOAD_URL,
    expected_sha256: str = SAMPLE_ARCHIVE_SHA256,
    expected_root: str = SAMPLE_ROOT_NAME,
    open_request: Callable[..., BinaryIO] = urlopen,
) -> Path:
    """Download the sample into destination and return its dataset root."""

    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / expected_root
    if target.exists():
        raise SampleDataError(f"sample dataset already exists: {target}")

    with tempfile.TemporaryDirectory(
        prefix=".dbslice-ai-sample-",
        dir=destination,
    ) as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / SAMPLE_ARCHIVE_NAME
        request = Request(url, headers={"User-Agent": "dbslice-ai-connector/sample"})
        digest = hashlib.sha256()
        with open_request(request, timeout=120) as response:
            with archive_path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)

        if digest.hexdigest() != expected_sha256:
            raise SampleDataError("sample download failed checksum verification")

        extraction_root = temporary_root / "extracted"
        extraction_root.mkdir()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _validate_archive(archive, expected_root)
                archive.extractall(extraction_root)
        except zipfile.BadZipFile as error:
            raise SampleDataError(
                "sample download is not a valid ZIP archive"
            ) from error

        extracted_dataset = extraction_root / expected_root
        if not (extracted_dataset / "config" / "config.json").is_file():
            raise SampleDataError(
                "sample archive does not contain a dbsliceAI dataset"
            )
        if target.exists():
            raise SampleDataError(f"sample dataset already exists: {target}")
        extracted_dataset.replace(target)

    return target
