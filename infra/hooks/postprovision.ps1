$ErrorActionPreference = "Stop"

Write-Host "Running postprovision hook..."

# Install Python dependencies first (needed for key fetching below)
python -m pip install -r src\notebooks\requirements.txt --quiet 2>$null

# Write .env and fetch API keys using azure-identity (no az CLI needed)
python infra\setup-env.py

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
