"""Create a Fabric Data Agent grounded in the Caldova medicinal product ontology.

The new Fabric ontology experience is queried by agents through a Fabric Data Agent. This
script creates the data agent, attaches the Caldova ontology as a data source, publishes it,
verifies that the published configuration contains the ontology, and only then writes
``FABRIC_DATA_AGENT_ID`` to ``.env`` for the Azure AI Search Fabric Data Agent knowledge source.

It calls the Fabric REST API directly with ``requests`` and ``azure-identity``. It does not use
``fabric-data-agent-sdk``, because that SDK depends on ``semantic-link-sempy``, which needs a
.NET runtime that the lab VM does not have.

REST reference: https://learn.microsoft.com/rest/api/fabric/dataagent/items
"""

import argparse
import json
import os
import time
from pathlib import Path

import requests
from azure.core.credentials import TokenCredential
from azure.identity import (
    AzureCliCredential,
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    DefaultAzureCredential,
)
from dotenv import load_dotenv, set_key

REPO_ROOT = Path(__file__).parents[1]
ENV_PATH = REPO_ROOT / ".env"
FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
DATA_AGENT_NAME = "CaldovaMedicinalProductDataAgent"
DATA_AGENT_DESCRIPTION = "Caldova medicinal product ontology for agentic retrieval."
WAIT_TIMEOUT_SECONDS = 300
WAIT_INTERVAL_SECONDS = 10
MAX_THROTTLE_RETRIES = 5

load_dotenv(dotenv_path=ENV_PATH, override=True)


def require_env(name: str) -> str:
    """Return a required environment setting."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required to create the Fabric Data Agent.")
    return value


def create_credential(tenant_id: str) -> TokenCredential:
    """Resolve a Fabric credential across Skillable, a signed-in az session, and azd."""
    if os.getenv("AZURE_CLIENT_ID") and os.getenv("AZURE_CLIENT_SECRET"):
        return DefaultAzureCredential()
    return ChainedTokenCredential(
        AzureCliCredential(tenant_id=tenant_id),
        AzureDeveloperCliCredential(tenant_id=tenant_id),
    )


class FabricApi:
    """Minimal Fabric REST client with bearer auth and throttling retries."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        for attempt in range(MAX_THROTTLE_RETRIES + 1):
            token = self._credential.get_token(FABRIC_SCOPE).token
            response = requests.request(
                method,
                f"{FABRIC_API}/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=120,
                **kwargs,
            )
            if response.status_code != 429 or attempt == MAX_THROTTLE_RETRIES:
                return response
            time.sleep(int(response.headers.get("Retry-After", WAIT_INTERVAL_SECONDS)))
        return response


def fail(action: str, response: requests.Response) -> RuntimeError:
    """Build a readable error from a failed Fabric response."""
    return RuntimeError(f"{action} failed with HTTP {response.status_code}: {response.text[:500]}")


def list_values(api: FabricApi, path: str, action: str) -> list[dict]:
    """Return all items from a paginated Fabric list endpoint."""
    items: list[dict] = []
    token = None
    while True:
        params = {"continuationToken": token} if token else None
        response = api.request("GET", path, params=params)
        if response.status_code != 200:
            raise fail(action, response)
        body = response.json() if response.text else {}
        items.extend(value for value in body.get("value", []) if isinstance(value, dict))
        token = body.get("continuationToken")
        if not token:
            return items


def find_agent_id(api: FabricApi, workspace_id: str) -> str | None:
    """Return the data agent ID by display name, or None."""
    for agent in list_values(api, f"workspaces/{workspace_id}/dataAgents", "List data agents"):
        if agent.get("displayName") == DATA_AGENT_NAME:
            return agent.get("id")
    return None


def get_or_create_agent(api: FabricApi, workspace_id: str) -> str:
    """Return the data agent ID, creating the agent when it does not exist."""
    agent_id = find_agent_id(api, workspace_id)
    if agent_id:
        print(f"Reusing data agent '{DATA_AGENT_NAME}' ({agent_id}).")
        return agent_id

    response = api.request(
        "POST",
        f"workspaces/{workspace_id}/dataAgents",
        json={"displayName": DATA_AGENT_NAME, "description": DATA_AGENT_DESCRIPTION},
    )
    if response.status_code == 201:
        agent_id = response.json().get("id")
        if agent_id:
            print(f"Created data agent '{DATA_AGENT_NAME}' ({agent_id}).")
            return agent_id
    elif response.status_code not in (202, 409):
        raise fail("Create data agent", response)

    # 202 means provisioning is still running. 409 means the name already exists.
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        agent_id = find_agent_id(api, workspace_id)
        if agent_id:
            print(f"Data agent '{DATA_AGENT_NAME}' is ready ({agent_id}).")
            return agent_id
        print("Waiting for the data agent to be provisioned...")
        time.sleep(WAIT_INTERVAL_SECONDS)
    raise RuntimeError(f"Data agent '{DATA_AGENT_NAME}' was not found after creation.")


def datasources_path(workspace_id: str, agent_id: str, stage: str) -> str:
    """Return the staging or published datasource collection path."""
    base = f"workspaces/{workspace_id}/dataAgents/{agent_id}"
    return f"{base}/staging/datasources" if stage == "staging" else f"{base}/datasources"


def has_ontology(entries: list[dict], ontology_id: str) -> bool:
    """Return True when any datasource entry references the ontology item."""
    needle = ontology_id.lower()
    return any(needle in json.dumps(entry).lower() for entry in entries)


def wait_for_ontology(api: FabricApi, workspace_id: str, agent_id: str, ontology_id: str, stage: str) -> None:
    """Poll until the ontology appears in the given configuration stage, or fail."""
    path = datasources_path(workspace_id, agent_id, stage)
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        if has_ontology(list_values(api, path, f"List {stage} datasources"), ontology_id):
            print(f"Ontology is attached in the {stage} configuration.")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"The ontology did not appear in the {stage} data agent configuration "
                f"within {WAIT_TIMEOUT_SECONDS} seconds."
            )
        print(f"Waiting for the ontology in the {stage} configuration...")
        time.sleep(WAIT_INTERVAL_SECONDS)


def attach_ontology(api: FabricApi, workspace_id: str, agent_id: str, ontology_id: str) -> None:
    """Attach the ontology to the staging configuration when it is not already attached."""
    path = datasources_path(workspace_id, agent_id, "staging")
    if has_ontology(list_values(api, path, "List staging datasources"), ontology_id):
        print("Ontology is already attached in the staging configuration.")
        return
    response = api.request(
        "POST",
        path,
        json={
            "type": "FabricItem",
            "itemReference": {
                "referenceType": "ById",
                "itemId": ontology_id,
                "workspaceId": workspace_id,
            },
        },
    )
    if response.status_code not in (200, 201, 202, 409):
        raise fail("Attach ontology", response)
    wait_for_ontology(api, workspace_id, agent_id, ontology_id, "staging")


def publish(api: FabricApi, workspace_id: str, agent_id: str) -> None:
    """Publish the staging configuration."""
    response = api.request(
        "POST",
        f"workspaces/{workspace_id}/dataAgents/{agent_id}/staging/publish",
        json={"publishedDescription": DATA_AGENT_DESCRIPTION},
    )
    if response.status_code not in (200, 202):
        raise fail("Publish data agent", response)
    print("Publish request accepted.")


def apply() -> None:
    """Create, bind, publish, and verify the Caldova ontology data agent."""
    tenant_id = os.getenv("FABRIC_TENANT_ID") or require_env("AZURE_TENANT_ID")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    ontology_id = require_env("FABRIC_ONTOLOGY_ID")

    api = FabricApi(create_credential(tenant_id))
    agent_id = get_or_create_agent(api, workspace_id)
    attach_ontology(api, workspace_id, agent_id, ontology_id)
    publish(api, workspace_id, agent_id)
    wait_for_ontology(api, workspace_id, agent_id, ontology_id, "published")

    set_key(ENV_PATH, "FABRIC_DATA_AGENT_ID", agent_id, quote_mode="never")
    print(f"Published data agent '{DATA_AGENT_NAME}' ({agent_id}).")


def dry_run() -> None:
    """Validate required inputs without changing Fabric resources."""
    required = ("AZURE_TENANT_ID", "FABRIC_WORKSPACE_ID", "FABRIC_ONTOLOGY_ID")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")
    print("Fabric Data Agent inputs are valid. No resources were changed.")


def main() -> None:
    """Parse arguments and run validation or create the data agent."""
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        dry_run() if args.dry_run else apply()
    except Exception as error:  # noqa: BLE001 - report any failure as a clean exit code
        raise SystemExit(f"ERROR: {error}") from None


if __name__ == "__main__":
    main()
