"""Create the Caldova supplier analytics lakehouse and load deterministic Delta tables."""

import argparse
import hashlib
import io
import json
import os
import re
import time
import uuid
import warnings
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from azure.core.credentials import TokenCredential
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient
from dotenv import load_dotenv, set_key

warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"microsoft_fabric_api\..*")

from microsoft_fabric_api import FabricClient  # noqa: E402
from microsoft_fabric_api.generated.core.models import (  # noqa: E402
    AddWorkspaceRoleAssignmentRequest,
    CreateWorkspaceRequest,
    UserPrincipal,
)
from microsoft_fabric_api.generated.lakehouse.models import (  # noqa: E402
    CreateLakehouseRequest,
    LoadTableRequest,
    Parquet,
)

REPO_ROOT = Path(__file__).parents[1]
ENV_PATH = REPO_ROOT / ".env"
DATA_ROOT = REPO_ROOT / "data" / "caldova" / "json"
SUPPLIER_PATH = DATA_ROOT / "suppliers.json"
INVOICE_PATH = DATA_ROOT / "waypoint-supplier-invoices.json"
PROFILE_PATH = DATA_ROOT / "supplier-kpi-profiles.json"
ONTOLOGY_DATA_PATH = DATA_ROOT / "medicinal-product-ontology.json"
ONELAKE_DFS_URL = "https://onelake.dfs.fabric.microsoft.com"
PORTAL_BASE_URL = "https://app.fabric.microsoft.com"
LAKEHOUSE_NAME = "CaldovaSupplierAnalytics"
SNAPSHOT_END = date(2026, 8, 17)
SNAPSHOT_WEEKS = 105

STATE_NAMES = {
    "CA": "California",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "MN": "Minnesota",
    "NC": "North Carolina",
    "NJ": "New Jersey",
    "TN": "Tennessee",
}

TABLE_SCHEMAS = {
    "DimDate": pa.schema(
        [
            pa.field("DateKey", pa.int32(), nullable=False),
            pa.field("Date", pa.date32()),
            pa.field("Year", pa.int32()),
            pa.field("Quarter", pa.int32()),
            pa.field("QuarterName", pa.string()),
            pa.field("MonthNumber", pa.int32()),
            pa.field("MonthName", pa.string()),
            pa.field("MonthYear", pa.string()),
            pa.field("MonthKey", pa.int32()),
            pa.field("WeekOfYear", pa.int32()),
            pa.field("DayOfWeekNumber", pa.int32()),
            pa.field("DayName", pa.string()),
            pa.field("IsWeekend", pa.string()),
        ]
    ),
    "DimSupplier": pa.schema(
        [
            pa.field("SupplierID", pa.string(), nullable=False),
            pa.field("SupplierName", pa.string()),
            pa.field("ServiceCategory", pa.string()),
            pa.field("LocationID", pa.string()),
            pa.field("Location", pa.string()),
            pa.field("ExperienceYears", pa.int32()),
            pa.field("AvailableCapacityKPerMonth", pa.int32()),
            pa.field("MOQKUnits", pa.int32()),
            pa.field("RegulatoryInspections3yr", pa.int32()),
            pa.field("FinancialStabilityRating", pa.string()),
        ]
    ),
    "SupplierPerformance": pa.schema(
        [
            pa.field("SupplierID", pa.string(), nullable=False),
            pa.field("DateKey", pa.int32(), nullable=False),
            pa.field("OTIF12moPct", pa.float64()),
            pa.field("BatchRejectionRatePct", pa.float64()),
            pa.field("RightFirstTimePct", pa.float64()),
            pa.field("CustomerComplaintsPer1k", pa.float64()),
            pa.field("LastRegulatoryInspection", pa.string()),
            pa.field("OpenRegulatoryActions", pa.int32()),
            pa.field("LastInternalAuditResult", pa.string()),
            pa.field("CurrentUtilizationPct", pa.float64()),
            pa.field("LeadTimeToFirstProductionWks", pa.int32()),
            pa.field("CostIndex", pa.int32()),
            pa.field("TechTransferSuccessPct", pa.float64()),
        ]
    ),
    "DimLocation": pa.schema(
        [
            pa.field("LocationID", pa.string(), nullable=False),
            pa.field("Location", pa.string()),
            pa.field("StateOrRegion", pa.string()),
            pa.field("Country", pa.string()),
        ]
    ),
    "DimInspectionResult": pa.schema(
        [
            pa.field("ResultCode", pa.string(), nullable=False),
            pa.field("ResultName", pa.string()),
            pa.field("Description", pa.string()),
            pa.field("SeverityRank", pa.int32()),
            pa.field("IsDisqualifying", pa.string()),
        ]
    ),
    "DimAuditResult": pa.schema(
        [
            pa.field("AuditResult", pa.string(), nullable=False),
            pa.field("ResultRank", pa.int32()),
            pa.field("IsPassing", pa.string()),
        ]
    ),
    "DimFinancialRating": pa.schema(
        [
            pa.field("Rating", pa.string(), nullable=False),
            pa.field("RatingRank", pa.int32()),
            pa.field("InvestmentGrade", pa.string()),
        ]
    ),
    "MedicinalProduct": pa.schema(
        [
            pa.field("ProductID", pa.string(), nullable=False),
            pa.field("Name", pa.string()),
            pa.field("DosageForm", pa.string()),
            pa.field("Strength", pa.string()),
            pa.field("RouteOfAdministration", pa.string()),
        ]
    ),
    "ActiveSubstance": pa.schema(
        [
            pa.field("SubstanceID", pa.string(), nullable=False),
            pa.field("PreferredName", pa.string()),
            pa.field("SubstanceType", pa.string()),
        ]
    ),
    "Manufacturer": pa.schema(
        [
            pa.field("ManufacturerID", pa.string(), nullable=False),
            pa.field("Name", pa.string()),
            pa.field("Country", pa.string()),
            pa.field("ApprovalStatus", pa.string()),
        ]
    ),
    "MarketingAuthorization": pa.schema(
        [
            pa.field("AuthorizationID", pa.string(), nullable=False),
            pa.field("AuthorizationNumber", pa.string()),
            pa.field("ProductID", pa.string(), nullable=False),
            pa.field("AgencyID", pa.string(), nullable=False),
            pa.field("Market", pa.string()),
            pa.field("Status", pa.string()),
            pa.field("EffectiveDate", pa.date32()),
        ]
    ),
    "RegulatoryAgency": pa.schema(
        [
            pa.field("AgencyID", pa.string(), nullable=False),
            pa.field("Name", pa.string()),
            pa.field("Abbreviation", pa.string()),
            pa.field("CountryOrRegion", pa.string()),
        ]
    ),
    "ProductActiveSubstance": pa.schema(
        [
            pa.field("ProductID", pa.string(), nullable=False),
            pa.field("SubstanceID", pa.string(), nullable=False),
        ]
    ),
    "ManufacturerActiveSubstance": pa.schema(
        [
            pa.field("ManufacturerID", pa.string(), nullable=False),
            pa.field("SubstanceID", pa.string(), nullable=False),
        ]
    ),
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


def resolve_capacity_id(client: FabricClient, capacity_id_or_arm: str) -> str:
    """Resolve an ARM capacity resource ID to the corresponding Fabric GUID."""
    if "/" not in capacity_id_or_arm:
        return capacity_id_or_arm

    capacity_name = capacity_id_or_arm.rstrip("/").split("/")[-1]
    for capacity in client.core.capacities.list_capacities():
        if capacity.display_name == capacity_name:
            return capacity.id
    raise RuntimeError(f"Fabric capacity '{capacity_name}' is not visible to this identity.")


def resolve_workspace(client: FabricClient) -> str:
    """Use an existing workspace or create one on the configured capacity."""
    workspace_id = os.getenv("FABRIC_WORKSPACE_ID", "").strip()
    if workspace_id:
        return workspace_id

    capacity_id = os.getenv("FABRIC_CAPACITY_ID", "").strip()
    if not capacity_id:
        raise RuntimeError("FABRIC_WORKSPACE_ID or FABRIC_CAPACITY_ID is required.")

    capacity_guid = resolve_capacity_id(client, capacity_id)
    workspace_name = f"CaldovaSupplyChain-{uuid.uuid4().hex[:8]}"
    workspace = client.core.workspaces.create_workspace(
        CreateWorkspaceRequest(
            display_name=workspace_name,
            description="Caldova supply-chain knowledge lab.",
            capacity_id=capacity_guid,
        )
    )
    set_key(ENV_PATH, "FABRIC_WORKSPACE_ID", workspace.id, quote_mode="never")
    print(f"Created Fabric workspace '{workspace_name}'.")
    return workspace.id


def add_lab_user(client: FabricClient, workspace_id: str) -> None:
    """Grant the Skillable attendee access to the service-principal-owned workspace."""
    user_id = os.getenv("FABRIC_LAB_USER_OID", "").strip()
    if not user_id:
        return
    try:
        client.core.workspaces.add_workspace_role_assignment(
            workspace_id,
            AddWorkspaceRoleAssignmentRequest(
                principal=UserPrincipal(id=user_id),
                role="Admin",
            ),
        )
    except HttpResponseError as error:
        if error.status_code != 409:
            raise


def stable_unit(*values: object) -> float:
    """Return a deterministic value in the interval [0, 1)."""
    digest = hashlib.sha256("|".join(map(str, values)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def bounded(value: float | None, lower: float, upper: float) -> float | None:
    """Clamp a nullable numeric value."""
    if value is None:
        return None
    return round(min(upper, max(lower, value)), 5)


def canonical_suppliers() -> list[dict[str, Any]]:
    """Read and validate the canonical supplier registry."""
    suppliers = json.loads(SUPPLIER_PATH.read_text())["suppliers"]
    supplier_ids = [row["supplier_id"] for row in suppliers]
    if len(supplier_ids) != len(set(supplier_ids)):
        raise ValueError("Canonical supplier IDs must be unique.")

    invoice_source = json.loads(INVOICE_PATH.read_text())
    invoice_ids = {row["supplier_id"] for row in invoice_source["invoices"]}
    document_profile_ids = {
        row["supplier_id"] for row in invoice_source["document_generation"]["profiles"]
    }
    if invoice_ids != document_profile_ids:
        raise ValueError("Invoice and billing-profile supplier sets differ.")
    unknown_invoice_ids = invoice_ids - set(supplier_ids)
    if unknown_invoice_ids:
        raise ValueError(f"Invoices reference unknown suppliers: {sorted(unknown_invoice_ids)}")
    return sorted(suppliers, key=lambda row: row["supplier_id"])


def normalize_location(address_lines: list[str]) -> tuple[str, str, str]:
    """Convert a billing address to reporting-friendly location fields."""
    country = address_lines[-1]
    locality = address_lines[-2]
    us_match = re.match(r"(.+), ([A-Z]{2}) \d{5}$", locality)
    if us_match:
        city, state_code = us_match.groups()
        region = STATE_NAMES.get(state_code, state_code)
        return f"{city}, {region}, {country}", region, country

    locality = re.sub(r"^\d{4}\s+", "", locality)
    locality = re.sub(r"\s+[A-Z]\d{2}\s+[A-Z0-9]{4}$", "", locality)
    locality = re.sub(r"\s+\d{5,6}$", "", locality)
    region = locality.split(",")[-1].strip()
    return f"{locality}, {country}" if locality != country else country, region, country


def profile_data() -> dict[str, dict[str, Any]]:
    """Read and validate synthetic KPI profiles."""
    payload = json.loads(PROFILE_PATH.read_text())
    profiles = payload["profiles"]
    profile_ids = [row["supplier_id"] for row in profiles]
    if payload["history_end_date"] != SNAPSHOT_END.isoformat():
        raise ValueError("Profile history end date does not match the generator contract.")
    if payload["snapshot_weeks"] != SNAPSHOT_WEEKS:
        raise ValueError("Profile snapshot count does not match the generator contract.")
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("Synthetic profile supplier IDs must be unique.")
    return {row["supplier_id"]: row for row in profiles}


def date_rows(snapshot_dates: list[date]) -> list[dict[str, Any]]:
    """Build a daily date dimension spanning all weekly snapshots."""
    rows = []
    current = snapshot_dates[0]
    while current <= snapshot_dates[-1]:
        quarter = (current.month - 1) // 3 + 1
        rows.append(
            {
                "DateKey": int(current.strftime("%Y%m%d")),
                "Date": current,
                "Year": current.year,
                "Quarter": quarter,
                "QuarterName": f"Q{quarter} {current.year}",
                "MonthNumber": current.month,
                "MonthName": current.strftime("%B"),
                "MonthYear": current.strftime("%b %Y"),
                "MonthKey": current.year * 100 + current.month,
                "WeekOfYear": current.isocalendar().week,
                "DayOfWeekNumber": current.weekday() + 1,
                "DayName": current.strftime("%A"),
                "IsWeekend": "Yes" if current.weekday() >= 5 else "No",
            }
        )
        current += timedelta(days=1)
    return rows


def performance_rows(
    profiles: dict[str, dict[str, Any]], snapshot_dates: list[date]
) -> list[dict[str, Any]]:
    """Build deterministic weekly supplier performance snapshots."""
    trend_sign = {"improving": 1, "stable": 0, "declining": -1}
    rows = []
    for supplier_id, profile in sorted(profiles.items()):
        baseline = profile["baseline"]
        direction = trend_sign[profile["trend"]]
        for index, snapshot in enumerate(snapshot_dates):
            progress = index / (len(snapshot_dates) - 1)
            recent = max(0.0, (progress - 0.95) / 0.05)

            def noise(metric: str, amplitude: float) -> float:
                return (stable_unit(supplier_id, snapshot, metric) - 0.5) * amplitude

            def metric(
                key: str, amplitude: float, shift: float, lower: float, upper: float
            ) -> float | None:
                value = baseline[key]
                if value is None:
                    return None
                return bounded(
                    value + noise(key, amplitude) + direction * recent * shift,
                    lower,
                    upper,
                )

            rows.append(
                {
                    "SupplierID": supplier_id,
                    "DateKey": int(snapshot.strftime("%Y%m%d")),
                    "OTIF12moPct": metric("otif", 0.012, 0.025, 0.75, 0.999),
                    "BatchRejectionRatePct": metric(
                        "batch_rejection", 0.004, -0.008, 0.001, 0.08
                    ),
                    "RightFirstTimePct": metric(
                        "right_first_time", 0.01, 0.018, 0.75, 0.999
                    ),
                    "CustomerComplaintsPer1k": metric(
                        "complaints_per_1k", 0.5, -0.5, 0.1, 8.0
                    ),
                    "LastRegulatoryInspection": profile["inspection"],
                    "OpenRegulatoryActions": profile["open_regulatory_actions"],
                    "LastInternalAuditResult": profile["audit"],
                    "CurrentUtilizationPct": metric(
                        "utilization", 0.025, -0.03, 0.3, 0.99
                    ),
                    "LeadTimeToFirstProductionWks": round(
                        max(
                            1,
                            baseline["lead_time_weeks"]
                            + noise("lead_time_weeks", 2.0)
                            - direction * recent * 2,
                        )
                    ),
                    "CostIndex": round(
                        max(
                            50,
                            baseline["cost_index"]
                            + noise("cost_index", 4.0)
                            - direction * recent * 2,
                        )
                    ),
                    "TechTransferSuccessPct": metric(
                        "tech_transfer_success", 0.01, 0.015, 0.5, 1.0
                    ),
                }
            )
    return rows


def ontology_rows(
    suppliers: list[dict[str, Any]], profiles: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Build ontology entity and relationship rows with endpoint validation."""
    source = json.loads(ONTOLOGY_DATA_PATH.read_text())
    supplier_by_id = {row["supplier_id"]: row for row in suppliers}

    products = [
        {
            "ProductID": row["product_id"],
            "Name": row["name"],
            "DosageForm": row["dosage_form"],
            "Strength": row["strength"],
            "RouteOfAdministration": row["route_of_administration"],
        }
        for row in source["medicinal_products"]
    ]
    substances = [
        {
            "SubstanceID": row["substance_id"],
            "PreferredName": row["preferred_name"],
            "SubstanceType": row["substance_type"],
        }
        for row in source["active_substances"]
    ]
    agencies = [
        {
            "AgencyID": row["agency_id"],
            "Name": row["name"],
            "Abbreviation": row["abbreviation"],
            "CountryOrRegion": row["country_or_region"],
        }
        for row in source["regulatory_agencies"]
    ]
    authorizations = [
        {
            "AuthorizationID": row["authorization_id"],
            "AuthorizationNumber": row["authorization_number"],
            "ProductID": row["product_id"],
            "AgencyID": row["agency_id"],
            "Market": row["market"],
            "Status": row["status"],
            "EffectiveDate": (
                date.fromisoformat(row["effective_date"])
                if row["effective_date"]
                else None
            ),
        }
        for row in source["marketing_authorizations"]
    ]
    product_substances = [
        {"ProductID": row["product_id"], "SubstanceID": row["substance_id"]}
        for row in source["product_active_substances"]
    ]
    manufacturer_substances = [
        {
            "ManufacturerID": row["manufacturer_id"],
            "SubstanceID": row["substance_id"],
        }
        for row in source["manufacturer_active_substances"]
    ]

    manufacturer_ids = {row["ManufacturerID"] for row in manufacturer_substances}
    manufacturers = []
    for manufacturer_id in sorted(manufacturer_ids):
        supplier = supplier_by_id.get(manufacturer_id)
        if supplier is None or supplier["service_category"] != "API manufacturing":
            raise ValueError(f"Manufacturer is not a canonical API supplier: {manufacturer_id}")
        _, _, country = normalize_location(supplier["address_lines"])
        manufacturers.append(
            {
                "ManufacturerID": manufacturer_id,
                "Name": supplier["supplier_name"],
                "Country": country,
                "ApprovalStatus": (
                    "Conditional"
                    if profiles[manufacturer_id]["audit"] == "Requires Improvement"
                    else "Approved"
                ),
            }
        )

    def unique_ids(rows: list[dict[str, Any]], key: str, label: str) -> set[str]:
        values = [row[key] for row in rows]
        if len(values) != len(set(values)):
            raise ValueError(f"{label} IDs must be unique.")
        return set(values)

    product_ids = unique_ids(products, "ProductID", "Product")
    substance_ids = unique_ids(substances, "SubstanceID", "Substance")
    agency_ids = unique_ids(agencies, "AgencyID", "Agency")
    unique_ids(authorizations, "AuthorizationID", "Authorization")
    if not all(
        row["ProductID"] in product_ids and row["AgencyID"] in agency_ids
        for row in authorizations
    ):
        raise ValueError("Marketing authorization contains an unknown endpoint key.")
    if not all(
        row["ProductID"] in product_ids and row["SubstanceID"] in substance_ids
        for row in product_substances
    ):
        raise ValueError("Product-substance relationship contains an unknown endpoint key.")
    if not all(
        row["ManufacturerID"] in manufacturer_ids
        and row["SubstanceID"] in substance_ids
        for row in manufacturer_substances
    ):
        raise ValueError("Manufacturer-substance relationship contains an unknown endpoint key.")
    if {row["ProductID"] for row in product_substances} != product_ids:
        raise ValueError("Every medicinal product must contain an active substance.")
    if {row["SubstanceID"] for row in manufacturer_substances} != substance_ids:
        raise ValueError("Every active substance must have an approved manufacturer.")

    return {
        "MedicinalProduct": products,
        "ActiveSubstance": substances,
        "Manufacturer": manufacturers,
        "MarketingAuthorization": authorizations,
        "RegulatoryAgency": agencies,
        "ProductActiveSubstance": product_substances,
        "ManufacturerActiveSubstance": manufacturer_substances,
    }


def build_tables() -> dict[str, pa.Table]:
    """Build every typed lakehouse table and validate the relational contract."""
    suppliers = canonical_suppliers()
    profiles = profile_data()
    supplier_ids = {row["supplier_id"] for row in suppliers}
    if set(profiles) != supplier_ids:
        raise ValueError("Canonical supplier and synthetic profile IDs must match.")

    locations = []
    supplier_rows = []
    for index, supplier in enumerate(suppliers, start=1):
        supplier_id = supplier["supplier_id"]
        profile = profiles[supplier_id]
        location, region, country = normalize_location(supplier["address_lines"])
        location_id = f"L{index:03d}"
        locations.append(
            {
                "LocationID": location_id,
                "Location": location,
                "StateOrRegion": region,
                "Country": country,
            }
        )
        static = profile["static"]
        supplier_rows.append(
            {
                "SupplierID": supplier_id,
                "SupplierName": supplier["supplier_name"],
                "ServiceCategory": supplier["service_category"],
                "LocationID": location_id,
                "Location": location,
                "ExperienceYears": static["experience_years"],
                "AvailableCapacityKPerMonth": static["available_capacity_k_per_month"],
                "MOQKUnits": static["moq_k_units"],
                "RegulatoryInspections3yr": static["regulatory_inspections_3yr"],
                "FinancialStabilityRating": static["financial_rating"],
            }
        )

    snapshot_dates = [
        SNAPSHOT_END - timedelta(weeks=offset)
        for offset in reversed(range(SNAPSHOT_WEEKS))
    ]
    performance = performance_rows(profiles, snapshot_dates)
    counts = Counter(row["SupplierID"] for row in performance)
    if set(counts) != supplier_ids or set(counts.values()) != {SNAPSHOT_WEEKS}:
        raise ValueError("Every supplier must have exactly 105 performance snapshots.")

    rows_by_table = {
        "DimDate": date_rows(snapshot_dates),
        "DimSupplier": supplier_rows,
        "SupplierPerformance": performance,
        "DimLocation": locations,
        "DimInspectionResult": [
            {
                "ResultCode": "NAI",
                "ResultName": "No Action Indicated",
                "Description": "No objectionable conditions or practices were found.",
                "SeverityRank": 1,
                "IsDisqualifying": "No",
            },
            {
                "ResultCode": "VAI",
                "ResultName": "Voluntary Action Indicated",
                "Description": "Voluntary correction is expected.",
                "SeverityRank": 2,
                "IsDisqualifying": "No",
            },
            {
                "ResultCode": "OAI",
                "ResultName": "Official Action Indicated",
                "Description": "Regulatory or administrative action is recommended.",
                "SeverityRank": 3,
                "IsDisqualifying": "Yes",
            },
        ],
        "DimAuditResult": [
            {"AuditResult": "Outstanding", "ResultRank": 1, "IsPassing": "Yes"},
            {"AuditResult": "Satisfactory", "ResultRank": 2, "IsPassing": "Yes"},
            {
                "AuditResult": "Requires Improvement",
                "ResultRank": 3,
                "IsPassing": "No",
            },
        ],
        "DimFinancialRating": [
            {"Rating": rating, "RatingRank": rank, "InvestmentGrade": investment}
            for rating, rank, investment in (
                ("A+", 1, "Yes"),
                ("A", 2, "Yes"),
                ("A-", 3, "Yes"),
                ("B+", 4, "Yes"),
                ("B", 5, "No"),
                ("B-", 6, "No"),
            )
        ],
    }
    rows_by_table.update(ontology_rows(suppliers, profiles))
    tables = {
        name: pa.Table.from_pylist(rows_by_table[name], schema=schema)
        for name, schema in TABLE_SCHEMAS.items()
    }
    if tables["DimSupplier"].num_rows != len(suppliers):
        raise ValueError("DimSupplier row count does not match the supplier registry.")
    if tables["SupplierPerformance"].num_rows != len(suppliers) * SNAPSHOT_WEEKS:
        raise ValueError("SupplierPerformance row count is incorrect.")
    return tables


def find_lakehouse(client: FabricClient, workspace_id: str):
    """Find the configured lakehouse by display name."""
    return next(
        (
            item
            for item in client.lakehouse.items.list_lakehouses(workspace_id)
            if item.display_name == LAKEHOUSE_NAME
        ),
        None,
    )


def parquet_bytes(table: pa.Table) -> bytes:
    """Serialize one Arrow table as Parquet."""
    output = io.BytesIO()
    pq.write_table(table, output)
    return output.getvalue()


def upload_table(
    credential: TokenCredential,
    client: FabricClient,
    workspace_id: str,
    lakehouse_id: str,
    table_name: str,
    table: pa.Table,
) -> None:
    """Upload Parquet and overwrite one managed Delta table."""
    relative_path = f"Files/supplier-analytics/{table_name}.parquet"
    filesystem = DataLakeServiceClient(
        account_url=ONELAKE_DFS_URL, credential=credential
    ).get_file_system_client(workspace_id)
    file_client = filesystem.get_file_client(f"{lakehouse_id}/{relative_path}")
    file_client.upload_data(parquet_bytes(table), overwrite=True)
    run_fabric_operation(
        lambda: client.lakehouse.tables.begin_load_table(
            workspace_id,
            lakehouse_id,
            table_name,
            LoadTableRequest(
                relative_path=relative_path,
                path_type="File",
                file_extension="parquet",
                mode="Overwrite",
                format_options=Parquet(),
            ),
        ).result()
    )


def delta_schema(
    credential: TokenCredential,
    workspace_id: str,
    lakehouse_id: str,
    table_name: str,
) -> dict[str, Any]:
    """Read the latest Delta metadata schema from OneLake."""
    filesystem = DataLakeServiceClient(
        account_url=ONELAKE_DFS_URL, credential=credential
    ).get_file_system_client(workspace_id)
    log_path = f"{lakehouse_id}/Tables/{table_name}/_delta_log"
    json_logs = sorted(
        path.name
        for path in filesystem.get_paths(path=log_path)
        if path.name.endswith(".json")
    )
    for path in reversed(json_logs):
        content = filesystem.get_file_client(path).download_file().readall().decode()
        for line in content.splitlines():
            action = json.loads(line)
            if "metaData" in action:
                return json.loads(action["metaData"]["schemaString"])
    raise RuntimeError(f"No Delta metadata schema found for {table_name}.")


def validate_delta_schema(expected: pa.Schema, actual: dict[str, Any], name: str) -> None:
    """Compare persisted Delta fields with the Arrow schema contract."""
    arrow_to_delta = {
        "string": "string",
        "int32": "integer",
        "double": "double",
        "date32[day]": "date",
    }
    expected_fields = [
        {
            "name": field.name,
            "type": arrow_to_delta[str(field.type)],
        }
        for field in expected
    ]
    actual_fields = [
        {
            "name": field["name"],
            "type": field["type"],
        }
        for field in actual["fields"]
    ]
    if actual_fields != expected_fields:
        raise ValueError(f"Persisted Delta schema mismatch for {name}.")


def wait_for_tables(
    client: FabricClient,
    workspace_id: str,
    lakehouse_id: str,
    expected_tables: set[str],
    attempts: int = 12,
) -> set[str]:
    """Wait for loaded Delta tables to appear in the Fabric catalog."""
    missing = expected_tables
    for attempt in range(1, attempts + 1):
        table_names = {
            item.name
            for item in client.lakehouse.tables.list_tables(workspace_id, lakehouse_id)
        }
        missing = expected_tables - table_names
        if not missing:
            return set()
        if attempt < attempts:
            print(
                f"Waiting for Fabric table catalog ({attempt}/{attempts}); "
                f"missing {len(missing)} table(s)..."
            )
            time.sleep(5)
    return missing


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


def create_lakehouse(client: FabricClient, workspace_id: str):
    """Create the lakehouse, tolerating the Fabric SDK long-running-operation bug.

    When microsoft-fabric-api raises KeyError('status') from its result callback, the
    lakehouse is still created server-side, so resolve it by name once it appears.
    """
    request = CreateLakehouseRequest(
        display_name=LAKEHOUSE_NAME,
        description="Synthetic supplier performance analytics for Caldova.",
    )
    try:
        return client.lakehouse.items.begin_create_lakehouse(workspace_id, request).result
    except KeyError:
        print("Fabric create-lakehouse poller returned no status; resolving by name...")
    for attempt in range(1, 13):
        lakehouse = find_lakehouse(client, workspace_id)
        if lakehouse is not None:
            return lakehouse
        print(f"Waiting for lakehouse to appear ({attempt}/12)...")
        time.sleep(5)
    raise RuntimeError(f"Lakehouse '{LAKEHOUSE_NAME}' was not found after creation failed.")


def deploy(tables: dict[str, pa.Table]) -> None:
    """Create or reuse the lakehouse and overwrite all supplier tables."""
    load_dotenv(ENV_PATH, override=False)
    tenant_id = os.getenv("FABRIC_TENANT_ID") or require_env("AZURE_TENANT_ID")
    credential = create_credential(tenant_id)
    try:
        client = FabricClient(credential)
        workspace_id = resolve_workspace(client)
        add_lab_user(client, workspace_id)
        lakehouse = find_lakehouse(client, workspace_id)
        if lakehouse is None:
            print(f"Creating lakehouse '{LAKEHOUSE_NAME}'...")
            lakehouse = create_lakehouse(client, workspace_id)
        else:
            print(f"Reusing lakehouse '{LAKEHOUSE_NAME}'.")

        for table_name, table in tables.items():
            print(f"Loading {table_name} ({table.num_rows:,} rows)...")
            for field in table.schema:
                if not field.nullable and table[field.name].null_count:
                    column_name = f"{table_name}.{field.name}"
                    raise ValueError(f"Non-nullable field contains nulls: {column_name}")
            upload_table(
                credential, client, workspace_id, lakehouse.id, table_name, table
            )
            validate_delta_schema(
                table.schema,
                delta_schema(credential, workspace_id, lakehouse.id, table_name),
                table_name,
            )

        missing = wait_for_tables(client, workspace_id, lakehouse.id, set(tables))
        if missing:
            raise RuntimeError(f"Fabric did not expose loaded tables: {sorted(missing)}")
    finally:
        credential.close()

    url = (
        f"{PORTAL_BASE_URL}/groups/{workspace_id}/lakehouses/{lakehouse.id}"
        "?experience=fabric-developer"
    )
    set_key(ENV_PATH, "FABRIC_LAKEHOUSE_URL", url, quote_mode="never")
    print(f"Lakehouse: {LAKEHOUSE_NAME} ({lakehouse.id})")
    print(f"Fabric UI: {url}")


def main() -> None:
    """Validate locally and optionally deploy to Fabric."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--validate-only", action="store_true", help="Build and validate without Fabric."
    )
    args = parser.parse_args()
    tables = build_tables()
    print(
        "Validated supplier tables: "
        + ", ".join(f"{name}={table.num_rows:,}" for name, table in tables.items())
    )
    if not args.validate_only:
        deploy(tables)


if __name__ == "__main__":
    main()