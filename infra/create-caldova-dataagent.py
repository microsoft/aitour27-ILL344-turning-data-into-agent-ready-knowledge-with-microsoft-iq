"""Create a Fabric Data Agent grounded in the Caldova medicinal product ontology.

The new Fabric ontology experience is queried by agents through a Fabric Data Agent, not the
older ``search_ontology`` tool. This script creates a data agent, adds the Caldova ontology as
a data source, publishes it, and writes ``FABRIC_DATA_AGENT_ID`` to ``.env`` so the Azure AI
Search Fabric Data Agent knowledge source can reference it.

See:
- https://learn.microsoft.com/fabric/data-science/data-agent-sdk
- https://learn.microsoft.com/fabric/iq/ontology/concepts-agent-integration
"""

import argparse
import json
import os
import time
from pathlib import Path

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
DATA_AGENT_NAME = "CaldovaMedicinalProductDataAgent"
WAIT_TIMEOUT_SECONDS = 300
WAIT_INTERVAL_SECONDS = 10
CREATE_ATTEMPTS = 6

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


def list_datasources(client, stage: str) -> list[dict]:
    """Return all staging or published datasource entries across pages."""
    fetch = (
        client.get_staging_datasources
        if stage == "staging"
        else client.get_published_datasources
    )
    entries: list[dict] = []
    token = None
    while True:
        response = fetch(token)
        values = response.get("value", []) if isinstance(response, dict) else []
        entries.extend(value for value in values if isinstance(value, dict))
        token = response.get("continuationToken") if isinstance(response, dict) else None
        if not token:
            return entries


def has_ontology(entries: list[dict], ontology_id: str) -> bool:
    """Return True when any datasource entry references the ontology item."""
    needle = ontology_id.lower()
    return any(needle in json.dumps(entry).lower() for entry in entries)


def wait_for_ontology(client, stage: str, ontology_id: str) -> None:
    """Poll until the ontology appears in the given stage, or fail."""
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        if has_ontology(list_datasources(client, stage), ontology_id):
            print(f"Ontology is attached in the {stage} configuration.")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"The ontology did not appear in the {stage} data agent configuration "
                f"within {WAIT_TIMEOUT_SECONDS} seconds."
            )
        print(f"Waiting for the ontology in the {stage} configuration...")
        time.sleep(WAIT_INTERVAL_SECONDS)


def get_or_create_agent(create_data_agent, workspace_id: str):
    """Create the data agent, or return it when it already exists.

    create_data_agent returns the existing agent when the display name is already in use.
    Retry because a newly created item can take a moment to become resolvable by name.
    """
    last_error: Exception | None = None
    for attempt in range(1, CREATE_ATTEMPTS + 1):
        try:
            return create_data_agent(DATA_AGENT_NAME, workspace_id=workspace_id)
        except Exception as error:  # noqa: BLE001 - retried, then surfaced
            last_error = error
            print(f"Data agent not ready yet ({attempt}/{CREATE_ATTEMPTS}): {error}")
            time.sleep(WAIT_INTERVAL_SECONDS)
    raise RuntimeError(f"Could not create or resolve the data agent: {last_error}")


def publish(management) -> None:
    """Publish the staging configuration, accepting an asynchronous 202 response."""
    try:
        management.publish_staging(
            description="Caldova medicinal product ontology for agentic retrieval."
        )
    except Exception as error:  # noqa: BLE001 - the SDK only accepts 200
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status != 202:
            raise
        print("Publish accepted and running asynchronously.")


def apply() -> None:
    """Create, bind, and publish the Caldova ontology data agent."""
    tenant_id = os.getenv("FABRIC_TENANT_ID") or require_env("AZURE_TENANT_ID")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    ontology_id = require_env("FABRIC_ONTOLOGY_ID")

    # These imports pull heavy Fabric analytics dependencies, so keep them local.
    from fabric.analytics.environment.credentials import (
        SetFabricAnalyticsDefaultTokenCredentialsGlobally,
    )
    from fabric.dataagent.client import create_data_agent

    SetFabricAnalyticsDefaultTokenCredentialsGlobally(create_credential(tenant_id))

    management = get_or_create_agent(create_data_agent, workspace_id)
    client = management._client
    print(f"Using data agent '{DATA_AGENT_NAME}' ({client.data_agent_id}).")

    if has_ontology(list_datasources(client, "staging"), ontology_id):
        print("Ontology is already attached in the staging configuration.")
    else:
        management.add_staging_datasource(ontology_id, workspace_id, type="ontology")
        wait_for_ontology(client, "staging", ontology_id)

    publish(management)
    wait_for_ontology(client, "published", ontology_id)

    data_agent_id = str(client.data_agent_id)
    set_key(ENV_PATH, "FABRIC_DATA_AGENT_ID", data_agent_id, quote_mode="never")
    print(f"Published data agent '{DATA_AGENT_NAME}' ({data_agent_id}).")


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
    if args.dry_run:
        dry_run()
    else:
        try:
            apply()
        except Exception as error:  # noqa: BLE001 - report any failure as a clean exit code
            raise SystemExit(f"ERROR: {error}") from None


if __name__ == "__main__":
    main()
