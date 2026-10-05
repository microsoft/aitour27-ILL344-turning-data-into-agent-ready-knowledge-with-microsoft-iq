"""Create or update the Caldova medicinal product Fabric IQ ontology."""

import argparse
import base64
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


# Lakehouse tables and their columns (TMDL data types).
TABLES = {
    "MedicinalProduct": [
        ("ProductID", "string"),
        ("Name", "string"),
        ("DosageForm", "string"),
        ("Strength", "string"),
        ("RouteOfAdministration", "string"),
    ],
    "ActiveSubstance": [
        ("SubstanceID", "string"),
        ("PreferredName", "string"),
        ("SubstanceType", "string"),
    ],
    "Manufacturer": [
        ("ManufacturerID", "string"),
        ("Name", "string"),
        ("Country", "string"),
        ("ApprovalStatus", "string"),
    ],
    "MarketingAuthorization": [
        ("AuthorizationID", "string"),
        ("AuthorizationNumber", "string"),
        ("ProductID", "string"),
        ("AgencyID", "string"),
        ("Market", "string"),
        ("Status", "string"),
        ("EffectiveDate", "dateTime"),
    ],
    "RegulatoryAgency": [
        ("AgencyID", "string"),
        ("Name", "string"),
        ("Abbreviation", "string"),
        ("CountryOrRegion", "string"),
    ],
    "ProductActiveSubstance": [("ProductID", "string"), ("SubstanceID", "string")],
    "ManufacturerActiveSubstance": [("ManufacturerID", "string"), ("SubstanceID", "string")],
}

# Entity name (also its backing table), description, key property, (property, column) pairs.
ENTITIES = [
    (
        "MedicinalProduct",
        "A medicinal product sold by Caldova.",
        "productId",
        [
            ("productId", "ProductID"),
            ("name", "Name"),
            ("dosageForm", "DosageForm"),
            ("strength", "Strength"),
            ("routeOfAdministration", "RouteOfAdministration"),
        ],
    ),
    (
        "ActiveSubstance",
        "An active pharmaceutical ingredient.",
        "substanceId",
        [
            ("substanceId", "SubstanceID"),
            ("preferredName", "PreferredName"),
            ("substanceType", "SubstanceType"),
        ],
    ),
    (
        "Manufacturer",
        "A manufacturer of active substances.",
        "manufacturerId",
        [
            ("manufacturerId", "ManufacturerID"),
            ("name", "Name"),
            ("country", "Country"),
            ("approvalStatus", "ApprovalStatus"),
        ],
    ),
    (
        "MarketingAuthorization",
        "A marketing authorization for a medicinal product in a market.",
        "authorizationId",
        [
            ("authorizationId", "AuthorizationID"),
            ("authorizationNumber", "AuthorizationNumber"),
            ("productId", "ProductID"),
            ("agencyId", "AgencyID"),
            ("market", "Market"),
            ("status", "Status"),
            ("effectiveDate", "EffectiveDate"),
        ],
    ),
    (
        "RegulatoryAgency",
        "A regulatory agency that issues marketing authorizations.",
        "agencyId",
        [
            ("agencyId", "AgencyID"),
            ("name", "Name"),
            ("abbreviation", "Abbreviation"),
            ("countryOrRegion", "CountryOrRegion"),
        ],
    ),
]

# Table relationships: name, many-side column, one-side column.
RELATIONSHIPS = [
    ("rel_pas_product", "ProductActiveSubstance.ProductID", "MedicinalProduct.ProductID"),
    ("rel_pas_substance", "ProductActiveSubstance.SubstanceID", "ActiveSubstance.SubstanceID"),
    ("rel_mas_manufacturer", "ManufacturerActiveSubstance.ManufacturerID", "Manufacturer.ManufacturerID"),
    ("rel_mas_substance", "ManufacturerActiveSubstance.SubstanceID", "ActiveSubstance.SubstanceID"),
    ("rel_authorization_product", "MarketingAuthorization.ProductID", "MedicinalProduct.ProductID"),
    ("rel_authorization_agency", "MarketingAuthorization.AgencyID", "RegulatoryAgency.AgencyID"),
]

# Entity relationships: name, from entity, to entity, backingConfiguration lines.
ENTITY_RELATIONSHIPS = [
    (
        "contains",
        "MedicinalProduct",
        "ActiveSubstance",
        [
            "type: table",
            "table: ProductActiveSubstance",
            "fromRelationship: rel_pas_product",
            "toRelationship: rel_pas_substance",
        ],
    ),
    (
        "manufactures",
        "Manufacturer",
        "ActiveSubstance",
        [
            "type: table",
            "table: ManufacturerActiveSubstance",
            "fromRelationship: rel_mas_manufacturer",
            "toRelationship: rel_mas_substance",
        ],
    ),
    ("hasAuthorization", "MedicinalProduct", "MarketingAuthorization", ["relationship: rel_authorization_product"]),
    ("issuedBy", "MarketingAuthorization", "RegulatoryAgency", ["relationship: rel_authorization_agency"]),
]

LINEAGE_NAMESPACE = uuid.UUID("6f1c2b0e-8a4d-4c55-9a51-0c1d2e3f4a5b")


def lineage_tag(*names: str) -> str:
    """Return a stable lineage tag so re-runs update elements in place."""
    return str(uuid.uuid5(LINEAGE_NAMESPACE, "/".join(names)))


def definition_part(path: str, text: str) -> OntologyDefinitionPart:
    """Encode one TMDL definition part."""
    return OntologyDefinitionPart(
        path=path,
        payload=base64.b64encode(text.encode()).decode("ascii"),
        payload_type="InlineBase64",
    )


def build_tmdl(workspace_id: str, lakehouse_id: str, sql_endpoint: str) -> dict[str, str]:
    """Build the new-experience (TMDL) ontology definition bound to the lakehouse."""
    expression = f"'DirectLake - {LAKEHOUSE_NAME}'"
    files = {
        "database.tmdl": "database\n\tcompatibilityLevel: 1000000\n",
        "namespaces/default.tmdl": "namespace default\n\tlineageTag: default\n",
        "expressions.tmdl": (
            f"expression {expression} =\n"
            "\t\tlet\n"
            "\t\t    Source = AzureStorage.DataLake("
            f'"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}", '
            "[HierarchicalNavigation=true])\n"
            "\t\tin\n"
            "\t\t    Source\n"
            f"\tlineageTag: {lineage_tag('expression', LAKEHOUSE_NAME)}\n"
        ),
    }
    annotations = {
        "ONT_WorkspaceId": workspace_id,
        "ONT_ItemId": lakehouse_id,
        "ONT_ItemKind": "Lakehouse",
        "ONT_ItemName": LAKEHOUSE_NAME,
        "ONT_SqlEndpoint": sql_endpoint,
        "ONT_SqlDatabase": LAKEHOUSE_NAME,
    }
    for table, columns in TABLES.items():
        lines = [f"table {table}", f"\tlineageTag: {lineage_tag('table', table)}", ""]
        for column, data_type in columns:
            lines += [
                f"\tcolumn {column}",
                f"\t\tdataType: {data_type}",
                f"\t\tlineageTag: {lineage_tag('column', table, column)}",
                f"\t\tsourceColumn: {column}",
                "",
            ]
        lines += [
            f"\tpartition {table} = entity",
            "\t\tmode: directLake",
            "\t\tsource",
            f"\t\t\tentityName: {table}",
            f"\t\t\texpressionSource: {expression}",
            "",
        ]
        lines += [f"\t\tannotation {key} = {value}\n" for key, value in annotations.items()]
        files[f"tables/{table}.tmdl"] = "\n".join(lines)

    for name, description, key_property, properties in ENTITIES:
        column_types = dict(TABLES[name])
        lines = [
            f"/// {description}",
            f"entity {name}",
            f"\tlineageTag: {lineage_tag('entity', name)}",
            f"\tbackingTable: {name}",
            f"\tkeyProperty: {key_property}",
            "",
        ]
        for prop, column in properties:
            lines += [
                f"\tproperty {prop}",
                f"\t\tdataType: {column_types[column]}",
                f"\t\tlineageTag: {lineage_tag('property', name, prop)}",
                "\t\tbackingConfiguration",
                f"\t\t\tvalueColumn: {name}.{column}",
                "",
            ]
        files[f"entities/{name}.tmdl"] = "\n".join(lines)

    files["relationships.tmdl"] = "\n".join(
        f"relationship {name}\n\tfromColumn: {from_column}\n\ttoColumn: {to_column}\n"
        for name, from_column, to_column in RELATIONSHIPS
    )
    files["entityRelationships.tmdl"] = "\n".join(
        f"entityRelationship {name}\n"
        f"\tlineageTag: {lineage_tag('entityRelationship', name)}\n"
        f"\tfromEntity: {from_entity}\n"
        f"\ttoEntity: {to_entity}\n\n"
        "\tbackingConfiguration\n" + "".join(f"\t\t{line}\n" for line in backing)
        for name, from_entity, to_entity, backing in ENTITY_RELATIONSHIPS
    )
    files["model.tmdl"] = (
        "model Model\n\n"
        + "".join(f"ref table {table}\n" for table in TABLES)
        + "".join(f"ref entity {entity[0]}\n" for entity in ENTITIES)
        + "\nref namespace default\n"
    )
    return files


def build_definition(workspace_id: str, lakehouse_id: str, sql_endpoint: str) -> OntologyDefinition:
    """Build the complete medicinal product ontology definition."""
    files = build_tmdl(workspace_id, lakehouse_id, sql_endpoint)
    return OntologyDefinition(parts=[definition_part(path, text) for path, text in files.items()])


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


def stored_part_paths(client: FabricClient, workspace_id: str, ontology_id: str) -> list[str]:
    """Return the part paths of the ontology definition stored in Fabric."""
    response = client.ontology.items.get_ontology_definition(workspace_id, ontology_id)
    return [part.path for part in response.definition.parts]


def create_ontology(client: FabricClient, workspace_id: str):
    """Create an empty ontology, which Fabric creates in the new TMDL-based experience."""
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
    except (KeyError, ResourceExistsError):
        ontology = None
    if ontology is None or not getattr(ontology, "id", None):
        ontology = wait_for_ontology(client, workspace_id)
    if ontology is None:
        raise RuntimeError(f"Ontology '{ONTOLOGY_NAME}' was not found after creation.")
    return ontology


def run_fabric_operation(operation) -> None:
    """Run a Fabric long-running operation, tolerating the SDK's KeyError('status') bug.

    microsoft-fabric-api raises KeyError('status') from its result callback when the
    completion payload omits the status envelope, even though the operation finishes
    server-side. Treat that specific error as success.
    """
    try:
        operation()
    except KeyError:
        print("Fabric long-running operation returned no status; treating as complete.")


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

        sql_endpoint = lakehouse.properties.sql_endpoint_properties.connection_string
        definition = build_definition(workspace_id, lakehouse.id, sql_endpoint)
        ontology = find_ontology(client, workspace_id)
        if ontology is None:
            ontology = create_ontology(client, workspace_id)
        else:
            if ontology.display_name != ONTOLOGY_NAME:
                ontology = client.ontology.items.update_ontology(
                    workspace_id,
                    ontology.id,
                    UpdateOntologyRequest(display_name=ONTOLOGY_NAME),
                )
            print(f"Reusing ontology '{ONTOLOGY_NAME}'.")
        run_fabric_operation(
            lambda: client.ontology.items.begin_update_ontology_definition(
                workspace_id,
                ontology.id,
                UpdateOntologyDefinitionRequest(definition=definition),
                update_metadata=False,
            ).result()
        )
        stored = set(stored_part_paths(client, workspace_id, ontology.id))
        missing = {part.path for part in definition.parts} - stored
        if missing:
            raise RuntimeError(f"The ontology definition is missing parts after setup: {sorted(missing)}")
        print("Ontology entities and lakehouse bindings are in place.")
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
    definition = build_definition("workspace-id", "lakehouse-id", "sql-endpoint")
    print(f"Validated ontology definition: {len(definition.parts)} parts")
    if not args.validate_only:
        deploy()


if __name__ == "__main__":
    main()