# Deploy to Your Own Azure Subscription

This folder contains resources for deploying the ILL344 knowledge base infrastructure to your own Azure subscription.

## Prerequisites

- **Azure subscription** with sufficient permissions to create resources
- **Azure Developer CLI (azd)** installed ([Install guide](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd))
- **Azure CLI** installed and configured ([Install guide](https://learn.microsoft.com/cli/azure/install-azure-cli))
- **Python 3.11+** installed
- **Git** (to clone this repository)
- **VS Code** or **GitHub Codespaces** with Jupyter extension (recommended)

### Required Azure Permissions

You'll need permissions to:

- Create resource groups
- Deploy Bicep templates
- Create and manage:
  - Foundry IQ (Azure AI Search) services
  - Microsoft Foundry projects
  - Azure OpenAI model deployments
- Assign Azure RBAC roles

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/microsoft/aitour27-ILL344-turning-data-into-agent-ready-knowledge-with-microsoft-iq.git
cd aitour27-ILL344-turning-data-into-agent-ready-knowledge-with-microsoft-iq
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Deploy with azd

```bash
azd auth login
azd up
```

This will:

- Provision all Azure resources (AI Search, Foundry project, OpenAI models, Fabric capacity)
- Fetch API keys and write a `.env` file with all required variables
- Create search indexes and upload sample data
- Set up the `CaldovaSupplierAnalytics` lakehouse and `CaldovaMedicinalProductOntology`

> **Note:** Email seeding (used in the hosted Skillable lab for Part 3 - Work IQ) requires a service principal with `Mail.Send` application permission and is **not run** during self-deploy. Part 3 will use your own mailbox data instead.

> **Work IQ prerequisites (Parts 3 and 4):** Work IQ requires (1) a usage-based billing plan (Copilot credits) for Work IQ configured in Copilot Studio, with each user assigned to it, (2) a Microsoft Entra Global Administrator enabling the Work IQ API in the tenant, and (3) an identity running `azd up` with the Cloud Application Administrator role (or the Microsoft Graph application permissions `Application.ReadWrite.All`, `DelegatedPermissionGrant.ReadWrite.All`, and `Directory.Read.All`), so `infra/create-workiq-entra.py` can create the Work IQ Entra app, grant `WorkIQAgent.Ask` admin consent, and add a federated credential for the search service identity. The provisioning hook is non-fatal, so Parts 1, 2, and 5 still deploy when Work IQ setup is unavailable. See [Create a Work IQ knowledge source](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-work-iq).

### 4. Start the Lab

Open the [notebooks](src/notebooks) folder in VS Code and **start with `part1-standard-foundry-iq-kb.ipynb`**.

## Cleanup

To delete all resources and avoid ongoing charges:

```bash
azd down
```

## Additional Resources

- [Foundry IQ (Azure AI Search) Documentation](https://learn.microsoft.com/azure/search/)
- [Azure OpenAI Service Documentation](https://learn.microsoft.com/azure/ai-services/openai/)
- [Azure Bicep Documentation](https://learn.microsoft.com/azure/azure-resource-manager/bicep/)
- [Microsoft Foundry Community Discord](https://aka.ms/AIFoundryDiscord-Ignite25)
