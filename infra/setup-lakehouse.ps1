<#
.SYNOPSIS
    Sets up the Caldova Fabric lakehouse and medicinal-product ontology.
.DESCRIPTION
    Creates a Python virtual environment, installs dependencies, and runs
    the Caldova provisioners using an existing workspace or deployed capacity.
    
    This script follows the same pattern as setup-knowledge.ps1 in the
    ILL344 AI Tour infra folder and can be called from a postprovision hook.
.PARAMETER WorkspaceId
    The Microsoft Fabric workspace GUID where the lakehouse will be created.
    If not provided, a workspace will be auto-created using CapacityId.
.PARAMETER CapacityId
    The Fabric capacity resource ID (from Bicep output). Used to auto-create a workspace.
.PARAMETER TenantId
    Microsoft Entra tenant ID to use for Fabric and OneLake authentication.
#>
param(
    [string]$WorkspaceId = "",
    [string]$CapacityId = "",
    [string]$TenantId = "",
    [string]$ClientId = "",
    [string]$ClientSecret = "",
    [string]$LabUserUpn = "",
    [string]$LabUserObjectId = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

if (-not $WorkspaceId -and -not $CapacityId) {
    throw "Either -WorkspaceId or -CapacityId must be provided."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = $scriptDir

Write-Output "==============================================" 
Write-Output " Caldova Fabric Lakehouse and Ontology Setup"
Write-Output "=============================================="
Write-Output ""

# Find Python
$pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $pythonCmd) { $pythonCmd = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $pythonCmd) { throw "Python 3.10+ is required. Please install Python." }

# Create venv
$venvPath = Join-Path $repoRoot ".venv"
if (-not (Test-Path $venvPath)) {
    Write-Output "Creating Python virtual environment..."
    python -m venv $venvPath
}

$venvPy = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { 
    # Linux/Mac fallback
    $venvPy = Join-Path $venvPath "bin/python"
}
if (-not (Test-Path $venvPy)) { throw "Venv python not found at $venvPy" }

# Install dependencies (reuse src/notebooks/requirements.txt which has all needed packages)
$repoParent = Split-Path $repoRoot -Parent
$reqFile = Join-Path (Join-Path (Join-Path $repoParent "src") "notebooks") "requirements.txt"
if (-not (Test-Path $reqFile)) {
    $reqFile = Join-Path $repoRoot "requirements.txt"
}
if (-not (Test-Path $reqFile)) { throw "No requirements file found" }

Write-Output "Installing Python dependencies..."
& $venvPy -m pip install --upgrade pip --quiet 2>$null
& $venvPy -m pip install -r $reqFile --quiet 2>$null

Write-Output "Running Caldova Fabric provisioners..."
Write-Output ""

# Set env vars for DefaultAzureCredential (EnvironmentCredential)
if ($ClientId) { [Environment]::SetEnvironmentVariable("AZURE_CLIENT_ID", $ClientId, "Process") }
if ($ClientSecret) { [Environment]::SetEnvironmentVariable("AZURE_CLIENT_SECRET", $ClientSecret, "Process") }
if ($TenantId) { [Environment]::SetEnvironmentVariable("AZURE_TENANT_ID", $TenantId, "Process") }
if ($TenantId) { [Environment]::SetEnvironmentVariable("FABRIC_TENANT_ID", $TenantId, "Process") }
if ($WorkspaceId) { [Environment]::SetEnvironmentVariable("FABRIC_WORKSPACE_ID", $WorkspaceId, "Process") }
if ($CapacityId) { [Environment]::SetEnvironmentVariable("FABRIC_CAPACITY_ID", $CapacityId, "Process") }
if ($LabUserUpn) { [Environment]::SetEnvironmentVariable("FABRIC_LAB_USER_UPN", $LabUserUpn, "Process") }
if ($LabUserObjectId) { [Environment]::SetEnvironmentVariable("FABRIC_LAB_USER_OID", $LabUserObjectId, "Process") }

Push-Location $repoRoot
$lakehouseScript = Join-Path $repoRoot "create-caldova-lakehouse.py"
$ontologyScript = Join-Path $repoRoot "create-caldova-ontology.py"
if (-not (Test-Path $lakehouseScript)) { throw "create-caldova-lakehouse.py not found" }
if (-not (Test-Path $ontologyScript)) { throw "create-caldova-ontology.py not found" }

& $venvPy $lakehouseScript
$exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
    & $venvPy $ontologyScript
    $exitCode = $LASTEXITCODE
}
Pop-Location

# Create the Fabric Data Agent over the ontology so Part 2 can query the new-experience
# ontology through the fabricDataAgent knowledge source. Runs in an isolated venv because
# fabric-data-agent-sdk pins azure-identity==1.17.1, which conflicts with the main venv.
# Non-fatal: this block must never stop the lakehouse and ontology setup, so errors are
# contained and native stderr (pip notices and warnings) cannot terminate the script.
if ($exitCode -eq 0) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        Write-Output ""
        Write-Output "Setting up Fabric Data Agent..."
        $daVenv = Join-Path $repoRoot ".venv-dataagent"
        if (-not (Test-Path $daVenv)) { python -m venv $daVenv }
        $daPy = Join-Path $daVenv "Scripts\python.exe"
        if (-not (Test-Path $daPy)) { $daPy = Join-Path $daVenv "bin/python" }
        if (-not (Test-Path $daPy)) {
            Write-Output "WARNING: Could not create the data agent venv; skipping Fabric Data Agent."
        } else {
            & $daPy -m pip install --upgrade pip --quiet 2>&1 | Out-Null
            & $daPy -m pip install --pre --quiet --timeout 120 --retries 5 fabric-data-agent-sdk python-dotenv 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Output "WARNING: Could not install fabric-data-agent-sdk (exit $LASTEXITCODE). Part 2 will not have FABRIC_DATA_AGENT_ID."
            } else {
                $dataAgentScript = Join-Path $repoRoot "create-caldova-dataagent.py"
                Push-Location $repoRoot
                & $daPy $dataAgentScript --apply 2>&1 | ForEach-Object { "$_" }
                $daExit = $LASTEXITCODE
                Pop-Location
                if ($daExit -eq 0) {
                    Write-Output "Fabric Data Agent setup complete"
                } else {
                    Write-Output "WARNING: Fabric Data Agent setup failed (exit $daExit). Part 2 will not have FABRIC_DATA_AGENT_ID."
                }
            }
        }
    } catch {
        Write-Output "WARNING: Fabric Data Agent setup error: $($_.Exception.Message)"
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

if ($exitCode -eq 0) {
    Write-Output ""
    Write-Output "Lakehouse and ontology setup completed successfully!"
} else {
    Write-Output ""
    Write-Output "ERROR: Caldova Fabric setup failed. Review the output above for details."
    exit $exitCode
}
