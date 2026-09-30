#!/bin/sh
set -e

echo "Running postprovision hook..."

# Install Python dependencies first (needed for key fetching below)
python3 -m pip install -r src/notebooks/requirements.txt --quiet 2>/dev/null

# Write .env and fetch API keys using azure-identity (no az CLI needed)
python3 infra/setup-env.py

# Restore the committed Caldova index snapshots
echo "Running knowledge setup..."
python3 infra/create-knowledge.py

# Set up Fabric when a workspace or capacity is configured
if [ -n "$FABRIC_WORKSPACE_ID" ] || [ -n "$FABRIC_CAPACITY_ID" ]; then
    echo "Setting up Fabric Lakehouse..."
    python3 infra/create-caldova-lakehouse.py
    python3 infra/create-caldova-ontology.py

    # Create the Fabric Data Agent over the ontology (Part 2). Uses the Fabric REST API
    # with packages already installed from requirements.txt. Non-fatal.
    echo "Setting up Fabric Data Agent..."
    python3 infra/create-caldova-dataagent.py --apply \
        || echo "WARN: Fabric Data Agent setup failed. Part 2 will not have FABRIC_DATA_AGENT_ID."
fi

# Note: Email seeding (seed-emails.ps1) requires a service principal with
# Mail.Send application permission and is only used in the Skillable hosted lab.
# For self-deploy, Part 4 (Work IQ) will use your own mailbox data.

echo "Postprovision complete! If there were no errors, you can open src/notebooks/ to start the lab."