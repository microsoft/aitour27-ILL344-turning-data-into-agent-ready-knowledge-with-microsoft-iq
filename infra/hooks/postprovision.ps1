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

    # Create the Fabric Data Agent over the ontology (Part 2). Isolated venv because
    # fabric-data-agent-sdk pins azure-identity==1.17.1. Non-fatal and contained.
    Write-Host "Setting up Fabric Data Agent..."
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        python -m venv .venv-dataagent
        .venv-dataagent\Scripts\python.exe -m pip install --pre --quiet --timeout 120 --retries 5 fabric-data-agent-sdk python-dotenv 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not install fabric-data-agent-sdk. Part 2 will not have FABRIC_DATA_AGENT_ID."
        } else {
            .venv-dataagent\Scripts\python.exe infra\create-caldova-dataagent.py --apply
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Fabric Data Agent setup failed. Part 2 will not have FABRIC_DATA_AGENT_ID."
            }
        }
    } catch {
        Write-Warning "Fabric Data Agent setup error: $($_.Exception.Message)"
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

Write-Host "Postprovision complete! If there were no errors, you can open src/notebooks/ to start the lab."
