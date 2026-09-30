"""Delete the per-lab Work IQ Microsoft Entra app and its service principal.

Teardown is idempotent and scoped to the exact application created for this lab instance,
identified by its application (client) ID. It resolves the client ID from WORK_IQ_ENTRA_APP_ID
(argument or environment) or from .env, never by a broad display-name search. Missing resources
are treated as already deleted.

Usage:
    python infra/delete-workiq-entra.py --apply
    python infra/delete-workiq-entra.py --apply --app-id <application-client-id>
"""

import argparse
import os
from pathlib import Path

import requests
from azure.core.credentials import TokenCredential
from azure.identity import (
    AzureCliCredential,
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    DefaultAzureCredential,
)
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parents[1]
ENV_PATH = REPO_ROOT / ".env"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

load_dotenv(dotenv_path=ENV_PATH, override=True)


def create_credential(tenant_id: str) -> TokenCredential:
    """Resolve a Graph credential across Skillable, a signed-in az session, and azd."""
    if os.getenv("AZURE_CLIENT_ID") and os.getenv("AZURE_CLIENT_SECRET"):
        return DefaultAzureCredential()
    return ChainedTokenCredential(
        AzureCliCredential(tenant_id=tenant_id),
        AzureDeveloperCliCredential(tenant_id=tenant_id),
    )


class GraphClient:
    """Minimal Microsoft Graph REST client with a bearer token."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential

    def _headers(self) -> dict[str, str]:
        token = self._credential.get_token(GRAPH_SCOPE).token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get(self, path: str) -> requests.Response:
        return requests.get(f"{GRAPH_BASE}{path}", headers=self._headers(), timeout=120)

    def delete(self, path: str) -> requests.Response:
        return requests.delete(f"{GRAPH_BASE}{path}", headers=self._headers(), timeout=120)


def resolve_object_id(response: requests.Response) -> str | None:
    """Return the object ID from a Graph lookup, or None when the resource is absent."""
    if response.status_code == 200:
        return response.json().get("id")
    if response.status_code == 404:
        return None
    raise RuntimeError(f"Microsoft Graph error {response.status_code}: {response.text}")


def delete_resource(graph: GraphClient, path: str, label: str) -> None:
    """Delete a Graph resource, treating a missing resource as already deleted."""
    response = graph.delete(path)
    if response.status_code in (204, 404):
        print(f"Deleted {label}." if response.status_code == 204 else f"{label} already gone.")
        return
    raise RuntimeError(f"Failed to delete {label}: {response.status_code} {response.text}")


def apply(app_id: str) -> None:
    """Delete the Work IQ service principal and application for the given client ID."""
    tenant_id = os.getenv("WORK_IQ_ENTRA_TENANT_ID") or os.getenv("AZURE_TENANT_ID", "")
    graph = GraphClient(create_credential(tenant_id))

    sp_object_id = resolve_object_id(graph.get(f"/servicePrincipals(appId='{app_id}')"))
    if sp_object_id:
        delete_resource(graph, f"/servicePrincipals/{sp_object_id}", "service principal")
    else:
        print("Service principal already gone.")

    app_object_id = resolve_object_id(graph.get(f"/applications(appId='{app_id}')"))
    if app_object_id:
        delete_resource(graph, f"/applications/{app_object_id}", "application registration")
    else:
        print("Application registration already gone.")

    print(f"Work IQ Entra teardown complete for application {app_id}.")


def main() -> None:
    """Parse arguments and delete the per-lab Work IQ Entra app."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--app-id", default=os.getenv("WORK_IQ_ENTRA_APP_ID", ""))
    args = parser.parse_args()

    app_id = args.app_id.strip()
    if not app_id:
        # Avoid broad deletion: without an exact client ID there is nothing safe to remove.
        print("No WORK_IQ_ENTRA_APP_ID provided. Nothing to delete.")
        return
    try:
        apply(app_id)
    except RuntimeError as error:
        raise SystemExit(f"ERROR: {error}") from None


if __name__ == "__main__":
    main()
