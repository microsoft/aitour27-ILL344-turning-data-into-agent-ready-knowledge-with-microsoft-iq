# GitHub Copilot App sidequest: Use your KB as an MCP server

Every Foundry IQ knowledge base exposes an MCP server, and you can use the GitHub Copilot App to ask questions that can be answered by that MCP server. Follow the instructions here to set up Copilot CLI.

## 1. Sign in to GitHub

Sign in through the [Skillable Events GitHub enterprise](https://github.com/enterprises/skillable-events/sso).

You will be signed into a special account created for the lab environment, not your actual GitHub account.

## 2. Sign in to GitHub Copilot App

Open the GitHub Copilot app by finding the shortcut on the desktop.

Follow the prompts to sign-in.

## 3. Add the KB MCP server

1. Open "Customize" from the left-hand sidebar
2. Open "MCP" from the top nav bar
3. Select the right-hand "Add" menu and select "MCP server" in the dropdown that pops up.
4. Fill in the details for the Caldova KB:

   * Server name: caldova-kb
   * Server type: HTTP (*not* Local/SSE)
   * URL: Paste in the URL printed by the notebook, of the form `https://${AZURE_SEARCH_SERVICE_NAME}.search.windows.net/knowledgebases/caldova-supply-chain-knowledge-base/mcp?api-version=2026-05-01-preview`
   * Headers: "api-key": AZURE_SEARCH_ADMIN_KEY

## 4. Ask a question grounded by the KB

Create a new chat by selecting "+" next to "Chats" in the sidebar.

Ask Copilot a question that matches the notebook you just ran. For example:

Use the Caldova knowledge base to compare Summit Dose's documented CALD-201 commitments across its RFP response, agreement amendment, and purchase order.

You can try asking the question without prefacing it with "Use the Caldova knowledge base", but the Copilot app agent may not choose to invoke the KB MCP server, as it may answer from its weights or using a different tool.
