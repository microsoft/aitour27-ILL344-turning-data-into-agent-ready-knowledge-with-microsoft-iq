$ErrorActionPreference = "Stop"

Write-Host "Running postprovision hook..."

# Install Python dependencies first (needed for key fetching below)
python -m pip install -r src\notebooks\requirements.txt --quiet 2>$null

# Write .env and fetch API keys using azure-identity (no az CLI needed)
python infra\setup-env.py

# Provision the per-lab Work IQ Entra app and federated credential for Parts 3 and 4.
# Non-fatal: needs an identity with app-registration and admin-consent rights, so the rest
# of the lab still provisions when Work IQ admin setup is unavailable.
$ErrorActionPreference = "Continue"
python infra\create-workiq-entra.py --apply
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Work IQ Entra setup was skipped or failed. Parts 3 and 4 require it."
}
$ErrorActionPreference = "Stop"

# Restore the committed Caldova index snapshots
Write-Host "Running knowledge setup..."
python infra\create-knowledge.py

# Set up Fabric when a workspace or capacity is configured
if ($env:FABRIC_WORKSPACE_ID -or $env:FABRIC_CAPACITY_ID) {
    Write-Host "Setting up Fabric Lakehouse..."
    python infra\create-caldova-lakehouse.py
    python infra\create-caldova-ontology.py
}

Write-Host "Postprovision complete! If there were no errors, you can open src/notebooks/ to start the lab."
