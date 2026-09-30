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
import os
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


def apply() -> None:
    """Create, bind, and publish the Caldova ontology data agent."""
    tenant_id = os.getenv("FABRIC_TENANT_ID") or require_env("AZURE_TENANT_ID")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    ontology_id = require_env("FABRIC_ONTOLOGY_ID")

    # These imports pull heavy Fabric analytics dependencies, so keep them local.
    from fabric.analytics.environment.credentials import (
        SetFabricAnalyticsDefaultTokenCredentialsGlobally,
    )
    from fabric.dataagent.client import create_data_agent, get_data_agent

    SetFabricAnalyticsDefaultTokenCredentialsGlobally(create_credential(tenant_id))

    try:
        management = get_data_agent(DATA_AGENT_NAME, workspace_id)
        print(f"Reusing existing data agent '{DATA_AGENT_NAME}'.")
    except Exception:
        management = create_data_agent(DATA_AGENT_NAME, workspace_id=workspace_id)
        print(f"Created data agent '{DATA_AGENT_NAME}'.")

    try:
        management.add_staging_datasource(ontology_id, workspace_id, type="ontology")
        print("Added the Caldova ontology as a data source.")
    except Exception as error:  # noqa: BLE001 - idempotent: source may already be attached
        print(f"Ontology data source already attached or not re-added: {error}")

    management.publish_staging(
        description="Caldova medicinal product ontology for agentic retrieval."
    )

    data_agent_id = str(management._client.data_agent_id)
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
        except RuntimeError as error:
            raise SystemExit(f"ERROR: {error}") from None


if __name__ == "__main__":
    main()
