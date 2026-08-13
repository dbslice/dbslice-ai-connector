from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dbslice_ai_connector.dataset_provider import (
    DatasetDefinition,
    DatasetOperationError,
    FilesystemDatasetProvider,
)
from dbslice_ai_connector.protocol_validation import (
    load_protocol_schema,
    validate_protocol_message,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_protocol_schema(REPO_ROOT / "protocol" / "v1" / "protocol.schema.json")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FilesystemDatasetProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        config = {
            "dataset": {"title": "Synthetic latency study"},
            "metaData": {
                "path": "data/metadata/items.json",
                "config": {
                    "title": "Synthetic latency study",
                    "description": "Transport fixture",
                },
            },
            "extracts": [
                {
                    "extractId": "preview",
                    "type": "image",
                    "description": "Synthetic preview",
                    "format": "png",
                    "path": "data/extracts/preview/${itemId}.png",
                    "embedding": {
                        "type": "grid",
                        "path": "data/extracts/preview/${itemId}_embedding.json",
                        "description": "Synthetic embedding",
                        "settings": {"shape": [1, 1]},
                    },
                },
                {
                    "extractId": "profile",
                    "type": "line",
                    "description": "Synthetic profile",
                    "format": "json",
                    "path": "data/extracts/profile/${itemId}.json",
                },
                {
                    "extractId": "geometry",
                    "type": "glb",
                    "description": "Synthetic geometry",
                    "format": "glb",
                    "path": "data/extracts/geometry/${itemId}.glb",
                },
            ],
        }
        self.items = [
            {"itemId": "case-001", "angle": 12.5},
            {"itemId": "case-002", "angle": 14},
        ]
        write_json(root / "config" / "config.json", config)
        write_json(root / "data" / "metadata" / "items.json", {"items": self.items})
        write_json(
            root / "data" / "extracts" / "profile" / "case-001.json",
            {
                "label": "Synthetic profile",
                "data": [{"x": 0, "y": 1}, {"x": 1, "y": 2}],
            },
        )
        write_json(
            root / "data" / "extracts" / "preview" / "case-001_embedding.json",
            {
                "shape": [1, 1],
                "cells": [{"index": [0, 0], "avg": 0.5}],
            },
        )
        (root / "data" / "extracts" / "preview" / "case-001.png").write_bytes(
            b"image"
        )
        (root / "data" / "extracts" / "geometry").mkdir(parents=True)
        (root / "data" / "extracts" / "geometry" / "case-001.glb").write_bytes(
            b"glb"
        )

        self.provider = FilesystemDatasetProvider(
            [
                DatasetDefinition(
                    alias="synthetic-study",
                    display_name="Synthetic latency study",
                    root=root,
                )
            ]
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_all_five_operations_and_wire_payloads(self) -> None:
        config = self.provider.execute("synthetic-study", "getDatasetConfig", {})
        self.assertEqual(config["dataset"]["title"], "Synthetic latency study")
        self.assertNotIn("path", config["metaData"])
        self.assertNotIn("path", config["extracts"][0])
        self.assertNotIn("path", config["extracts"][0]["embedding"])

        self.assertEqual(
            self.provider.execute("synthetic-study", "getItems", {}),
            self.items,
        )
        self.assertEqual(
            self.provider.execute(
                "synthetic-study",
                "getItemById",
                {"itemId": "case-001"},
            ),
            self.items[0],
        )
        self.assertEqual(
            self.provider.execute(
                "synthetic-study",
                "getItemsBatch",
                {"itemIds": ["case-002", "missing"]},
            ),
            {
                "items": [self.items[1]],
                "missingItemIds": ["missing"],
            },
        )

        for extract_id, kind in (
            ("preview", "image"),
            ("profile", "line"),
            ("geometry", "glb"),
            ("preview", "embedding"),
        ):
            result = self.provider.execute(
                "synthetic-study",
                "readExtractPayload",
                {
                    "itemId": "case-001",
                    "extractId": extract_id,
                    "kind": kind,
                },
            )
            validate_protocol_message(
                {
                    "protocolVersion": "1",
                    "messageType": "operation.success",
                    "requestId": f"req_{kind}fixture",
                    "operation": "readExtractPayload",
                    "ok": True,
                    "result": result,
                },
                schema=SCHEMA,
            )

    def test_missing_item_maps_to_not_found(self) -> None:
        with self.assertRaises(DatasetOperationError) as context:
            self.provider.execute(
                "synthetic-study",
                "getItemById",
                {"itemId": "missing"},
            )
        self.assertEqual(context.exception.code, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
