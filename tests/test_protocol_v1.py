from __future__ import annotations

import json
import hashlib
from pathlib import Path
import unittest

from dbslice_ai_connector.protocol_validation import (
    ProtocolValidationError,
    load_protocol_schema,
    validate_protocol_message,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ROOT = REPO_ROOT / "protocol" / "v1"
FIXTURES_ROOT = PROTOCOL_ROOT / "fixtures"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMA = load_protocol_schema(PROTOCOL_ROOT / "protocol.schema.json")
CASES = load_json(FIXTURES_ROOT / "cases.json")["cases"]
MANIFEST = load_json(PROTOCOL_ROOT / "manifest.json")


def fixtures_sha256(fixtures_root: Path) -> str:
    digest = hashlib.sha256()
    for fixture_path in sorted(path for path in fixtures_root.rglob("*") if path.is_file()):
        relative_path = fixture_path.relative_to(fixtures_root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(fixture_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class ProtocolV1FixtureTest(unittest.TestCase):
    def test_manifest_matches_schema(self):
        schema_bytes = (PROTOCOL_ROOT / MANIFEST["schemaFile"]).read_bytes()
        self.assertEqual(MANIFEST["protocolVersion"], "1")
        self.assertEqual(
            hashlib.sha256(schema_bytes).hexdigest(),
            MANIFEST["schemaSha256"],
        )
        self.assertEqual(
            fixtures_sha256(FIXTURES_ROOT),
            MANIFEST["fixturesSha256"],
        )

    def test_protocol_fixtures(self):
        for case in CASES:
            with self.subTest(fixture=case["file"]):
                message = load_json(FIXTURES_ROOT / case["file"])

                if case["valid"]:
                    validate_protocol_message(message, schema=SCHEMA)
                    continue

                with self.assertRaises(ProtocolValidationError):
                    validate_protocol_message(message, schema=SCHEMA)


if __name__ == "__main__":
    unittest.main()
