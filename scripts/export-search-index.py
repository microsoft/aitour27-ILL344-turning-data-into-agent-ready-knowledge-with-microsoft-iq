"""Export validated portable Azure AI Search index snapshots."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from dotenv import load_dotenv


load_dotenv(override=True)

REPO_ROOT = Path(__file__).parents[1]
OUTPUT_ROOT = REPO_ROOT / "data" / "index-data"
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
FORBIDDEN_NAMES = {
    "image_path",
    "user_ids",
    "group_ids",
    "permission_filter",
    "permissionFilter",
}
VECTOR_DIMENSIONS = 3072


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("indexes", nargs="*", choices=INDEX_NAMES)
    return parser.parse_args()


def serialized_index(index: Any) -> dict[str, Any]:
    if hasattr(index, "serialize"):
        return index.serialize()
    if hasattr(index, "as_dict"):
        return index.as_dict()
    raise RuntimeError("The installed Search SDK cannot serialize index definitions.")


def find_forbidden_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_NAMES:
                found.add(key)
            found.update(find_forbidden_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(find_forbidden_names(child))
    return found


def make_portable(index_definition: dict[str, Any]) -> None:
    index_definition.pop("@odata.context", None)
    index_definition.pop("@odata.etag", None)
    if index_definition.get("permissionFilterOption") is not None:
        raise RuntimeError("Index definition contains a non-null permission filter option.")
    index_definition.pop("permissionFilterOption", None)
    for field in index_definition.get("fields", []):
        if field.get("permissionFilter") is not None:
            raise RuntimeError("Index definition contains a non-null permission filter.")
        field.pop("permissionFilter", None)
    forbidden = find_forbidden_names(index_definition)
    if forbidden:
        raise RuntimeError(f"Index definition contains forbidden fields: {sorted(forbidden)}")
    schema_fields = [field["name"] for field in index_definition.get("fields", [])]
    if schema_fields != list(FIELDS):
        raise RuntimeError(f"Unexpected index schema: {schema_fields}")
    vectorizers = index_definition.get("vectorSearch", {}).get("vectorizers", [])
    if not vectorizers:
        raise RuntimeError("Index definition has no vectorizer.")
    parameters = vectorizers[0].get("azureOpenAIParameters", {})
    if "resourceUri" in parameters:
        parameters["resourceUri"] = None
    elif "resourceUrl" in parameters:
        parameters["resourceUrl"] = None
    else:
        raise RuntimeError("Azure OpenAI vectorizer endpoint was not found.")


def normalize_record(index_name: str, record: dict[str, Any]) -> dict[str, Any]:
    record = {key: value for key, value in record.items() if not key.startswith("@search.")}
    forbidden = set(record).intersection(FORBIDDEN_NAMES)
    if forbidden:
        raise RuntimeError(f"Record contains forbidden fields: {sorted(forbidden)}")
    missing = set(FIELDS).difference(record)
    extra = set(record).difference(FIELDS)
    if missing or extra:
        raise RuntimeError(f"Record fields differ from schema; missing={missing}, extra={extra}")
    vector = record["text_vector"]
    if not isinstance(vector, list) or len(vector) != VECTOR_DIMENSIONS:
        raise RuntimeError(f"Record {record['chunk_id']} has an invalid text vector.")
    source_path = unquote(urlparse(record["blob_path"]).path)
    filename = Path(source_path).name
    if not filename.lower().endswith(".pdf"):
        raise RuntimeError(f"Record {record['chunk_id']} has an invalid blob path.")
    record["blob_path"] = f"/{index_name}/{filename}"
    return {field: record[field] for field in FIELDS}


async def export_index(endpoint: str, credential: Any, index_name: str) -> None:
    async with SearchIndexClient(endpoint, credential) as index_client:
        index_definition = serialized_index(await index_client.get_index(index_name))
    make_portable(index_definition)

    async with SearchClient(endpoint, index_name, credential) as search_client:
        source_count = await search_client.get_document_count()
        results = await search_client.search("*", select=list(FIELDS))
        records = [
            normalize_record(index_name, dict(result)) async for result in results
        ]
    records.sort(key=lambda record: record["chunk_id"])
    keys = [record["chunk_id"] for record in records]
    if not records or len(keys) != len(set(keys)):
        raise RuntimeError(f"Index '{index_name}' is empty or contains duplicate keys.")
    if len(records) != source_count:
        raise RuntimeError(
            f"Exported {len(records)} records from '{index_name}', expected {source_count}."
        )

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    index_path = OUTPUT_ROOT / f"{index_name}-index.json"
    records_path = OUTPUT_ROOT / f"{index_name}-exported.jsonl"
    index_path.write_text(json.dumps(index_definition, indent=2) + "\n", encoding="utf-8")
    with records_path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n")
    print(f"Exported {len(records)} records from '{index_name}' to {records_path}.")


async def main() -> None:
    endpoint = os.environ["AZURE_SEARCH_SERVICE_ENDPOINT"]
    search_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
    identity_credential = DefaultAzureCredential()
    credential: Any = AzureKeyCredential(search_key) if search_key else identity_credential
    try:
        for index_name in parse_args().indexes or INDEX_NAMES:
            await export_index(endpoint, credential, index_name)
    finally:
        await identity_credential.close()


if __name__ == "__main__":
    asyncio.run(main())