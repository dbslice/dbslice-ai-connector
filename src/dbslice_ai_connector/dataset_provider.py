"""Filesystem-backed implementation of the connector V1 dataset operations."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rfc8785

MAX_DECODED_PAYLOAD_BYTES = 16 * 1024 * 1024
DATASET_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class DatasetOperationError(RuntimeError):
    """An operation error that can be represented on the connector wire."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details


@dataclass(frozen=True)
class DatasetDefinition:
    """One connector-local alias and its filesystem root."""

    alias: str
    root: Path
    display_name: str

    def __post_init__(self) -> None:
        if not DATASET_ALIAS_PATTERN.fullmatch(self.alias):
            raise ValueError(
                "dataset ID must start with a letter or number and contain at "
                "most 80 letters, numbers, dots, underscores or hyphens"
            )
        if not self.display_name.strip():
            raise ValueError("dataset display name must not be empty")
        object.__setattr__(self, "display_name", self.display_name.strip())
        object.__setattr__(self, "root", self.root.expanduser().resolve())


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:80]


def dataset_definition_from_root(
    root: Path,
    *,
    dataset_id: str | None = None,
) -> DatasetDefinition:
    """Read a dataset's public identity from its configuration."""

    resolved_root = root.expanduser().resolve()
    config_path = resolved_root / "config" / "config.json"
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as error:
        raise ValueError(
            f"dataset does not contain config/config.json: {resolved_root}"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read dataset configuration: {error}") from error

    dataset = config.get("dataset") if isinstance(config, dict) else None
    title = dataset.get("title") if isinstance(dataset, dict) else None
    if not isinstance(title, str) or not title.strip():
        metadata = config.get("metaData") if isinstance(config, dict) else None
        metadata_config = metadata.get("config") if isinstance(metadata, dict) else None
        title = (
            metadata_config.get("title")
            if isinstance(metadata_config, dict)
            else None
        )
    if not isinstance(title, str) or not title.strip():
        title = resolved_root.name

    alias = dataset_id or _slug(title)
    if not alias:
        alias = "dataset"
    return DatasetDefinition(alias=alias, display_name=title, root=resolved_root)


def _canonical_json_bytes(value: Any) -> bytes:
    return rfc8785.dumps(value)


def _fingerprint(content: bytes) -> dict[str, str]:
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(content).hexdigest(),
    }


def _finite_number(value: Any, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DatasetOperationError(
            "INVALID_REQUEST",
            f"{field_name} must be a number",
        )
    if not math.isfinite(value):
        raise DatasetOperationError(
            "INVALID_REQUEST",
            f"{field_name} must be finite",
        )
    return value


def _public_extract(extract: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "extractId",
        "type",
        "description",
        "format",
        "xLabel",
        "yLabel",
        "filter",
        "render",
    }
    result = {key: extract[key] for key in allowed if key in extract}
    embedding = extract.get("embedding")
    if isinstance(embedding, dict):
        public_embedding = {
            key: embedding[key]
            for key in ("type", "source", "method", "description", "settings")
            if key in embedding
        }
        if "source" not in public_embedding:
            public_embedding["source"] = (
                "computed" if embedding.get("method") else "file"
            )
        result["embedding"] = public_embedding
    return result


class FilesystemDatasetProvider:
    """Serve configured aliases without exposing connector-local paths."""

    def __init__(self, datasets: list[DatasetDefinition]) -> None:
        self.datasets = {dataset.alias: dataset for dataset in datasets}
        if len(self.datasets) != len(datasets):
            raise ValueError("dataset aliases must be unique")

    def advertisements(self) -> list[dict[str, Any]]:
        advertisements = []
        for dataset in self.datasets.values():
            config = self._load_raw_config(dataset)
            public_config = self._public_config(config)
            content = _canonical_json_bytes(public_config)
            advertisements.append(
                {
                    "datasetAlias": dataset.alias,
                    "displayName": dataset.display_name,
                    "fingerprint": _fingerprint(content),
                }
            )
        return advertisements

    def execute(self, dataset_alias: str, operation: str, args: dict[str, Any]) -> Any:
        dataset = self.datasets.get(dataset_alias)
        if dataset is None:
            raise DatasetOperationError(
                "NOT_FOUND",
                f"Dataset alias was not found: {dataset_alias}",
                details={"datasetAlias": dataset_alias},
            )

        if operation == "getDatasetConfig":
            return self._public_config(self._load_raw_config(dataset))
        if operation == "getItems":
            return self._load_items(dataset)
        if operation == "getItemById":
            return self._get_item_by_id(dataset, args["itemId"])
        if operation == "getItemsBatch":
            return self._get_items_batch(dataset, args["itemIds"])
        if operation == "readExtractPayload":
            return self._read_extract_payload(dataset, args)
        raise DatasetOperationError(
            "INVALID_REQUEST",
            f"Unsupported dataset operation: {operation}",
        )

    def _load_raw_config(self, dataset: DatasetDefinition) -> dict[str, Any]:
        config_path = self._contained_file(dataset.root, "config/config.json")
        return self._read_json(config_path)

    def _public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        metadata = config.get("metaData")
        metadata_config = metadata.get("config") if isinstance(metadata, dict) else None
        extracts = config.get("extracts")
        if (
            not isinstance(config.get("dataset"), dict)
            or not isinstance(metadata_config, dict)
            or not isinstance(extracts, list)
        ):
            raise DatasetOperationError(
                "INVALID_REQUEST",
                "Dataset configuration is missing dataset, metaData.config, or extracts",
            )
        return {
            "dataset": config["dataset"],
            "metaData": {"config": metadata_config},
            "extracts": [_public_extract(extract) for extract in extracts],
        }

    def _load_items(self, dataset: DatasetDefinition) -> list[dict[str, Any]]:
        config = self._load_raw_config(dataset)
        metadata_path = config.get("metaData", {}).get("path")
        payload = self._read_json(self._contained_file(dataset.root, metadata_path))
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or any(
            not isinstance(item, dict) or not item.get("itemId") for item in items
        ):
            raise DatasetOperationError(
                "INVALID_REQUEST",
                "Dataset metadata must contain an items array with itemId values",
            )
        return items

    def _get_item_by_id(
        self,
        dataset: DatasetDefinition,
        item_id: str,
    ) -> dict[str, Any]:
        item = next(
            (entry for entry in self._load_items(dataset) if entry["itemId"] == item_id),
            None,
        )
        if item is None:
            raise DatasetOperationError(
                "NOT_FOUND",
                f'Item with ID "{item_id}" was not found',
                details={"itemId": item_id},
            )
        return item

    def _get_items_batch(
        self,
        dataset: DatasetDefinition,
        item_ids: list[str],
    ) -> dict[str, Any]:
        index = {item["itemId"]: item for item in self._load_items(dataset)}
        return {
            "items": [index[item_id] for item_id in item_ids if item_id in index],
            "missingItemIds": [
                item_id for item_id in item_ids if item_id not in index
            ],
        }

    def _read_extract_payload(
        self,
        dataset: DatasetDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = args["itemId"]
        extract_id = args["extractId"]
        kind = args["kind"]
        self._get_item_by_id(dataset, item_id)
        config = self._load_raw_config(dataset)
        extract = next(
            (
                entry
                for entry in config.get("extracts", [])
                if entry.get("extractId") == extract_id
            ),
            None,
        )
        if extract is None:
            raise DatasetOperationError(
                "NOT_FOUND",
                f"Extract was not found: {extract_id}",
                details={"extractId": extract_id},
            )

        if kind == "embedding":
            embedding = extract.get("embedding")
            if not isinstance(embedding, dict):
                raise DatasetOperationError(
                    "NOT_FOUND",
                    f"Extract has no embedding: {extract_id}",
                )
            source = embedding.get("source") or (
                "computed" if embedding.get("method") else "file"
            )
            if source != "file" or not embedding.get("path"):
                raise DatasetOperationError(
                    "INVALID_REQUEST",
                    f"Computed embedding has no connector source payload: {extract_id}",
                )
            template = embedding["path"]
        else:
            if extract.get("type") != kind:
                raise DatasetOperationError(
                    "INVALID_REQUEST",
                    f"Extract {extract_id} is {extract.get('type')}, not {kind}",
                )
            template = extract.get("path")

        relative_path = self._resolve_template(template, item_id)
        file_path = self._contained_file(dataset.root, relative_path)
        content = file_path.read_bytes()
        if len(content) > MAX_DECODED_PAYLOAD_BYTES:
            raise DatasetOperationError(
                "TOO_LARGE",
                f"Extract payload exceeds {MAX_DECODED_PAYLOAD_BYTES} bytes",
            )

        if kind in {"image", "glb"}:
            encoded = base64.b64encode(content).decode("ascii")
            return {
                "kind": kind,
                "contentType": self._content_type(kind, extract, file_path),
                "encoding": "base64",
                "data": encoded,
                "encodedSizeBytes": len(encoded.encode("ascii")),
                "decodedSizeBytes": len(content),
                "fingerprint": _fingerprint(content),
            }

        parsed = json.loads(content.decode("utf-8"))
        if kind == "line":
            points = parsed if isinstance(parsed, list) else parsed.get("data")
            if not isinstance(points, list):
                raise DatasetOperationError(
                    "INVALID_REQUEST",
                    f"Line extract must contain a data array: {extract_id}",
                )
            data: dict[str, Any] = {
                "points": [
                    {
                        "x": _finite_number(point["x"], "line point x"),
                        "y": _finite_number(point["y"], "line point y"),
                    }
                    for point in points
                ]
            }
            if isinstance(parsed, dict) and isinstance(parsed.get("label"), str):
                data["label"] = parsed["label"]
        else:
            if not isinstance(parsed, dict):
                raise DatasetOperationError(
                    "INVALID_REQUEST",
                    f"Embedding payload must be an object: {extract_id}",
                )
            embedding_type = extract["embedding"].get("type", "grid")
            data = {"type": embedding_type, **parsed}

        canonical = _canonical_json_bytes(data)
        if len(canonical) > MAX_DECODED_PAYLOAD_BYTES:
            raise DatasetOperationError(
                "TOO_LARGE",
                f"Extract payload exceeds {MAX_DECODED_PAYLOAD_BYTES} bytes",
            )
        return {
            "kind": kind,
            "contentType": "application/json",
            "encoding": "json",
            "data": data,
            "encodedSizeBytes": len(canonical),
            "fingerprint": _fingerprint(canonical),
        }

    @staticmethod
    def _resolve_template(template: Any, item_id: str) -> str:
        if not isinstance(template, str) or not template:
            raise DatasetOperationError(
                "INVALID_REQUEST",
                "Extract path is missing",
            )
        resolved = template.replace("${itemId}", item_id)
        if "${" in resolved:
            raise DatasetOperationError(
                "INVALID_REQUEST",
                f"Extract path contains an unresolved template: {resolved}",
            )
        return resolved

    @staticmethod
    def _contained_file(root: Path, relative_path: Any) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise DatasetOperationError(
                "INVALID_REQUEST",
                "Dataset path must be a non-empty string",
            )
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise DatasetOperationError(
                "UNAUTHORIZED",
                "Dataset path resolves outside its configured root",
            ) from error
        if not candidate.is_file():
            raise DatasetOperationError(
                "NOT_FOUND",
                f"Dataset file was not found: {relative_path}",
            )
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DatasetOperationError(
                "INVALID_REQUEST",
                f"Could not read JSON dataset file: {path.name}",
            ) from error
        if not isinstance(value, dict):
            raise DatasetOperationError(
                "INVALID_REQUEST",
                f"JSON dataset file must contain an object: {path.name}",
            )
        return value

    @staticmethod
    def _content_type(
        kind: str,
        extract: dict[str, Any],
        file_path: Path,
    ) -> str:
        if kind == "glb":
            return "model/gltf-binary"
        extension = str(extract.get("format") or file_path.suffix.removeprefix(".")).lower()
        return {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "svg": "image/svg+xml",
        }.get(extension, "image/octet-stream")
