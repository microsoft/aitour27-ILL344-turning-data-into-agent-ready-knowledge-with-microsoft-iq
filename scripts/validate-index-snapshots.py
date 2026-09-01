"""Validate committed portable Azure AI Search snapshots without Azure access."""

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).parents[1]
INDEX_DATA_ROOT = REPO_ROOT / "data" / "index-data"
INDEX_NAMES = ("supplier-evidence", "sourcing-documents")
FIELDS = (
    "chunk_id",
    "parent_id",
    "title",
    "blob_path",
    "chunk",
    "page_number_from",
    "page_number_to",
    "text_vector",
)
FORBIDDEN_TERMS = (
    "@odata.context",
    "@odata.etag",
    "image_path",
    "user_ids",
    "group_ids",
    "permissionFilter",
    "blob.core.windows.net",
    "cognitiveservices.azure.com",
    "services.ai.azure.com",
)
VECTOR_DIMENSIONS = 3072


def walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(walk_keys(child))
    return keys


def validate_index(index_name: str) -> int:
    index_path = INDEX_DATA_ROOT / f"{index_name}-index.json"
    records_path = INDEX_DATA_ROOT / f"{index_name}-exported.jsonl"
    index_text = index_path.read_text(encoding="utf-8")
    records_text = records_path.read_text(encoding="utf-8")
    if not index_text.endswith("\n") or not records_text.endswith("\n"):
        raise RuntimeError(f"Snapshot files for '{index_name}' need trailing newlines.")
    combined_text = index_text + records_text
    leaked_terms = [term for term in FORBIDDEN_TERMS if term in combined_text]
    if leaked_terms:
        raise RuntimeError(f"Snapshot '{index_name}' contains forbidden terms: {leaked_terms}")

    index_definition = json.loads(index_text)
    schema_fields = [field["name"] for field in index_definition.get("fields", [])]
    if schema_fields != list(FIELDS):
        raise RuntimeError(f"Snapshot '{index_name}' has unexpected fields: {schema_fields}")
    if walk_keys(index_definition).intersection({"image_path", "user_ids", "group_ids"}):
        raise RuntimeError(f"Snapshot '{index_name}' contains excluded schema fields.")

    lines = records_text.splitlines()
    records = [json.loads(line) for line in lines]
    keys = [record.get("chunk_id") for record in records]
    if not records or keys != sorted(keys) or len(keys) != len(set(keys)):
        raise RuntimeError(f"Snapshot '{index_name}' records are empty, unordered, or duplicated.")
    for record in records:
        if tuple(record) != FIELDS:
            raise RuntimeError(f"Record {record.get('chunk_id')} does not match the schema.")
        if record["blob_path"] != f"/{index_name}/{Path(record['blob_path']).name}":
            raise RuntimeError(f"Record {record['chunk_id']} has a nonportable blob path.")
        if len(record["text_vector"]) != VECTOR_DIMENSIONS:
            raise RuntimeError(f"Record {record['chunk_id']} has an invalid vector.")
    return len(records)


def main() -> None:
    for index_name in INDEX_NAMES:
        count = validate_index(index_name)
        print(f"Validated {count} records in '{index_name}'.")


if __name__ == "__main__":
    main()