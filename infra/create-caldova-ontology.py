"""Create or update the Caldova medicinal product Fabric IQ ontology."""

import argparse
import base64
import json
import os
import time
import uuid
import warnings
from pathlib import Path

from azure.core.credentials import TokenCredential
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from dotenv import load_dotenv, set_key

warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"microsoft_fabric_api\..*")

from microsoft_fabric_api import FabricClient  # noqa: E402
from microsoft_fabric_api.generated.ontology.models import (  # noqa: E402
    CreateOntologyRequest,
    OntologyDefinition,
    OntologyDefinitionPart,
    UpdateOntologyDefinitionRequest,
    UpdateOntologyRequest,
)

REPO_ROOT = Path(__file__).parents[1]
ENV_PATH = REPO_ROOT / ".env"
PORTAL_BASE_URL = "https://app.fabric.microsoft.com"
LAKEHOUSE_NAME = "CaldovaSupplierAnalytics"
ONTOLOGY_NAME = "CaldovaMedicinalProductOntology"

REQUIRED_TABLES = {
    "MedicinalProduct",
    "ActiveSubstance",
    "Manufacturer",
    "MarketingAuthorization",
    "RegulatoryAgency",
    "ProductActiveSubstance",
    "ManufacturerActiveSubstance",
}


def require_env(name: str) -> str:
    """Return a required environment setting."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for Fabric deployment.")
    return value


def create_credential(tenant_id: str) -> TokenCredential:
    """Use a service principal in Skillable and azd credentials for local setup."""
    if os.getenv("AZURE_CLIENT_ID") and os.getenv("AZURE_CLIENT_SECRET"):
        return DefaultAzureCredential()
    return AzureDeveloperCliCredential(tenant_id=tenant_id)


def definition_part(path: str, payload: dict) -> OntologyDefinitionPart:
    """Encode one ontology item definition part."""
    encoded = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode("ascii")
    return OntologyDefinitionPart(
        path=path,
        payload=encoded,
        payload_type="InlineBase64",
    )


def entity_parts(
    entity_id: int,
    entity_name: str,
    table_name: str,
    columns: list[dict],
    key_property: str,
    display_property: str,
    workspace_id: str,
    lakehouse_id: str,
) -> list[OntologyDefinitionPart]:
    """Build an entity definition and its lakehouse binding."""
    properties = []
    property_bindings = []
    for offset, column in enumerate(columns, start=1):
        property_id = str(entity_id * 100 + offset)
        properties.append(
            {
                "id": property_id,
                "name": column["name"],
                "valueType": column["type"],
            }
        )
        property_bindings.append(
            {
                "sourceColumnName": column["source"],
                "targetPropertyId": property_id,
            }
        )

    property_ids = {item["name"]: item["id"] for item in properties}
    definition = {
        "id": str(entity_id),
        "namespace": "usertypes",
        "baseEntityTypeId": None,
        "name": entity_name,
        "entityIdParts": [property_ids[key_property]],
        "displayNamePropertyId": property_ids[display_property],
        "namespaceType": "Custom",
        "visibility": "Visible",
        "properties": properties,
        "timeseriesProperties": [],
    }
    binding_id = str(uuid.uuid4())
    binding = {
        "id": binding_id,
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "timestampColumnName": None,
            "propertyBindings": property_bindings,
            "sourceTableProperties": {
                "sourceType": "LakehouseTable",
                "workspaceId": workspace_id,
                "itemId": lakehouse_id,
                "sourceTableName": table_name,
            },
        },
    }
    return [
        definition_part(f"EntityTypes/{entity_id}/definition.json", definition),
        definition_part(
            f"EntityTypes/{entity_id}/DataBindings/{binding_id}.json", binding
        ),
    ]


def relationship_parts(
    relationship_id: int,
    relationship_name: str,
    source_entity_id: int,
    source_column: str,
    source_property_id: str,
    target_entity_id: int,
    target_column: str,
    target_property_id: str,
    table_name: str,
    workspace_id: str,
    lakehouse_id: str,
) -> list[OntologyDefinitionPart]:
    """Build a relationship definition and its lakehouse contextualization."""
    definition = {
        "namespace": "usertypes",
        "id": str(relationship_id),
        "name": relationship_name,
        "namespaceType": "Custom",
        "source": {"entityTypeId": str(source_entity_id)},
        "target": {"entityTypeId": str(target_entity_id)},
    }
    contextualization_id = str(uuid.uuid4())
    contextualization = {
        "id": contextualization_id,
        "dataBindingTable": {
            "workspaceId": workspace_id,
            "itemId": lakehouse_id,
            "sourceTableName": table_name,
            "sourceType": "LakehouseTable",
        },
        "sourceKeyRefBindings": [
            {
                "sourceColumnName": source_column,
                "targetPropertyId": source_property_id,
            }
        ],
        "targetKeyRefBindings": [
            {
                "sourceColumnName": target_column,
                "targetPropertyId": target_property_id,
            }
        ],
    }
    return [
        definition_part(
            f"RelationshipTypes/{relationship_id}/definition.json", definition
        ),
        definition_part(
            f"RelationshipTypes/{relationship_id}/Contextualizations/"
            f"{contextualization_id}.json",
            contextualization,
        ),
    ]


def build_definition(workspace_id: str, lakehouse_id: str) -> OntologyDefinition:
    """Build the complete medicinal product ontology definition."""
    parts = [
        definition_part(
            ".platform",
            {"metadata": {"type": "Ontology", "displayName": ONTOLOGY_NAME}},
        ),
        definition_part("definition.json", {}),
    ]
    entity_specs = [
        (
            1001,
            "MedicinalProduct",
            "MedicinalProduct",
            [
                {"name": "productId", "source": "ProductID", "type": "String"},
                {"name": "name", "source": "Name", "type": "String"},
                {"name": "dosageForm", "source": "DosageForm", "type": "String"},
                {"name": "strength", "source": "Strength", "type": "String"},
                {
                    "name": "routeOfAdministration",
                    "source": "RouteOfAdministration",
                    "type": "String",
                },
            ],
            "productId",
            "name",
        ),
        (
            1002,
            "ActiveSubstance",
            "ActiveSubstance",
            [
                {
                    "name": "substanceId",
                    "source": "SubstanceID",
                    "type": "String",
                },
                {
                    "name": "preferredName",
                    "source": "PreferredName",
                    "type": "String",
                },
                {
                    "name": "substanceType",
                    "source": "SubstanceType",
                    "type": "String",
                },
            ],
            "substanceId",
            "preferredName",
        ),
        (
            1003,
            "Manufacturer",
            "Manufacturer",
            [
                {
                    "name": "manufacturerId",
                    "source": "ManufacturerID",
                    "type": "String",
                },
                {"name": "name", "source": "Name", "type": "String"},
                {"name": "country", "source": "Country", "type": "String"},
                {
                    "name": "approvalStatus",
                    "source": "ApprovalStatus",
                    "type": "String",
                },
            ],
            "manufacturerId",
            "name",
        ),
        (
            1004,
            "MarketingAuthorization",
            "MarketingAuthorization",
            [
                {
                    "name": "authorizationId",
                    "source": "AuthorizationID",
                    "type": "String",
                },
                {
                    "name": "authorizationNumber",
                    "source": "AuthorizationNumber",
                    "type": "String",
                },
                {"name": "productId", "source": "ProductID", "type": "String"},
                {"name": "agencyId", "source": "AgencyID", "type": "String"},
                {"name": "market", "source": "Market", "type": "String"},
                {"name": "status", "source": "Status", "type": "String"},
                {
                    "name": "effectiveDate",
                    "source": "EffectiveDate",
                    "type": "DateTime",
                },
            ],
            "authorizationId",
            "authorizationNumber",
        ),
        (
            1005,
            "RegulatoryAgency",
            "RegulatoryAgency",
            [
                {"name": "agencyId", "source": "AgencyID", "type": "String"},
                {"name": "name", "source": "Name", "type": "String"},
                {
                    "name": "abbreviation",
                    "source": "Abbreviation",
                    "type": "String",
                },
                {
                    "name": "countryOrRegion",
                    "source": "CountryOrRegion",
                    "type": "String",
                },
            ],
            "agencyId",
            "name",
        ),
    ]
    for spec in entity_specs:
        parts.extend(entity_parts(*spec, workspace_id, lakehouse_id))

    relationship_specs = [
        (
            2001,
            "contains",
            1001,
            "ProductID",
            "100101",
            1002,
            "SubstanceID",
            "100201",
            "ProductActiveSubstance",
        ),
        (
            2002,
            "manufactures",
            1003,
            "ManufacturerID",
            "100301",
            1002,
            "SubstanceID",
            "100201",
            "ManufacturerActiveSubstance",
        ),
        (
            2003,
            "hasAuthorization",
            1001,
            "ProductID",
            "100101",
            1004,
            "AuthorizationID",
            "100401",
            "MarketingAuthorization",
        ),
        (
            2004,
            "issuedBy",
            1004,
            "AuthorizationID",
            "100401",
            1005,
            "AgencyID",
            "100501",
            "MarketingAuthorization",
        ),
    ]
    for spec in relationship_specs:
        parts.extend(relationship_parts(*spec, workspace_id, lakehouse_id))
    return OntologyDefinition(parts=parts)


def find_lakehouse(client: FabricClient, workspace_id: str):
    """Find the lakehouse containing the bound ontology tables."""
    return next(
        (
            item
            for item in client.lakehouse.items.list_lakehouses(workspace_id)
            if item.display_name == LAKEHOUSE_NAME
        ),
        None,
    )


def find_ontology(client: FabricClient, workspace_id: str):
    """Find the ontology by configured ID or stable display name."""
    ontology_id = os.getenv("FABRIC_ONTOLOGY_ID", "").strip()
    if ontology_id:
        return client.ontology.items.get_ontology(workspace_id, ontology_id)
    return next(
        (
            item
            for item in client.ontology.items.list_ontologies(workspace_id)
            if item.display_name == ONTOLOGY_NAME
        ),
        None,
    )


def wait_for_ontology(
    client: FabricClient,
    workspace_id: str,
    attempts: int = 12,
):
    """Wait for a newly created ontology to appear in the item catalog."""
    for attempt in range(1, attempts + 1):
        ontology = find_ontology(client, workspace_id)
        if ontology is not None:
            return ontology
        if attempt < attempts:
            print(f"Waiting for ontology catalog ({attempt}/{attempts})...")
            time.sleep(5)
    return None


def wait_for_required_tables(
    client: FabricClient,
    workspace_id: str,
    lakehouse_id: str,
    attempts: int = 12,
) -> set[str]:
    """Wait for ontology source tables to appear in the Fabric catalog."""
    missing = REQUIRED_TABLES
    for attempt in range(1, attempts + 1):
        table_names = {
            item.name
            for item in client.lakehouse.tables.list_tables(workspace_id, lakehouse_id)
        }
        missing = REQUIRED_TABLES - table_names
        if not missing:
            return set()
        if attempt < attempts:
            print(
                f"Waiting for ontology tables ({attempt}/{attempts}); "
                f"missing {len(missing)} table(s)..."
            )
            time.sleep(5)
    return missing


def deploy() -> None:
    """Create or update the ontology in an existing Fabric workspace."""
    load_dotenv(ENV_PATH, override=False)
    tenant_id = os.getenv("FABRIC_TENANT_ID") or require_env("AZURE_TENANT_ID")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    credential = create_credential(tenant_id)
    try:
        client = FabricClient(credential)
        lakehouse = find_lakehouse(client, workspace_id)
        if lakehouse is None:
            raise RuntimeError(f"Lakehouse '{LAKEHOUSE_NAME}' does not exist.")

        missing = wait_for_required_tables(client, workspace_id, lakehouse.id)
        if missing:
            raise RuntimeError(f"Ontology lakehouse tables are missing: {sorted(missing)}")

        ontology = find_ontology(client, workspace_id)
        if ontology is None:
            print(f"Creating ontology '{ONTOLOGY_NAME}'...")
            try:
                ontology = client.ontology.items.create_ontology(
                    workspace_id,
                    CreateOntologyRequest(
                        display_name=ONTOLOGY_NAME,
                        description=(
                            "Medicinal products, active substances, manufacturers, "
                            "and authorizations for Caldova."
                        ),
                    ),
                )
            except ResourceExistsError:
                ontology = wait_for_ontology(client, workspace_id)
                if ontology is None:
                    raise RuntimeError(
                        f"Ontology '{ONTOLOGY_NAME}' exists but is not discoverable."
                    ) from None
        elif ontology.display_name != ONTOLOGY_NAME:
            ontology = client.ontology.items.update_ontology(
                workspace_id,
                ontology.id,
                UpdateOntologyRequest(display_name=ONTOLOGY_NAME),
            )
        else:
            print(f"Reusing ontology '{ONTOLOGY_NAME}'.")

        client.ontology.items.begin_update_ontology_definition(
            workspace_id,
            ontology.id,
            UpdateOntologyDefinitionRequest(
                definition=build_definition(workspace_id, lakehouse.id)
            ),
            update_metadata=False,
        ).result()
    finally:
        credential.close()

    ui_url = (
        f"{PORTAL_BASE_URL}/groups/{workspace_id}/ontologies/{ontology.id}"
        "?experience=fabric-developer"
    )
    mcp_url = (
        "https://api.fabric.microsoft.com/v1/mcp/dataPlane/"
        f"workspaces/{workspace_id}/items/{ontology.id}/ontologyEndpoint"
    )
    set_key(ENV_PATH, "FABRIC_ONTOLOGY_ID", ontology.id, quote_mode="never")
    set_key(ENV_PATH, "FABRIC_ONTOLOGY_UI_URL", ui_url, quote_mode="never")
    set_key(ENV_PATH, "FABRIC_ONTOLOGY_MCP_URL", mcp_url, quote_mode="never")
    print(f"Ontology: {ONTOLOGY_NAME} ({ontology.id})")
    print(f"Fabric UI: {ui_url}")
    print(f"Ontology MCP: {mcp_url}")


def main() -> None:
    """Validate the definition locally and optionally deploy it."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--validate-only", action="store_true", help="Build the definition without Fabric."
    )
    args = parser.parse_args()
    definition = build_definition("workspace-id", "lakehouse-id")
    print(f"Validated ontology definition: {len(definition.parts)} parts")
    if not args.validate_only:
        deploy()


if __name__ == "__main__":
    main()