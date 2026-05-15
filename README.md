<!-- Begin standard disclaimer — do not modify -->
**IMPORTANT!** All samples and other resources made available in this GitHub repository ("samples") are designed to assist in accelerating development of agents, solutions, and agent workflows for various scenarios. Review all provided resources and carefully test output behavior in the context of your use case. AI responses may be inaccurate and AI actions should be monitored with human oversight. Learn more in the transparency documents for [Agent Service](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/agents/transparency-note) and [Agent Framework](https://github.com/microsoft/agent-framework/blob/main/TRANSPARENCY_FAQ.md).

Agents, solutions, or other output you create may be subject to legal and regulatory requirements, may require licenses, or may not be suitable for all industries, scenarios, or use cases. By using any sample, you are acknowledging that any output created using those samples are solely your responsibility, and that you will comply with all applicable laws, regulations, and relevant safety standards, terms of service, and codes of conduct.

Third-party samples contained in this folder are subject to their own designated terms, and they have not been tested or verified by Microsoft or its affiliates.

Microsoft has no responsibility to you or others with respect to any of these samples or any resulting output.
<!-- End standard disclaimer -->

# LangGraph Toolbox Agent (Responses Protocol)

A LangGraph ReAct agent that connects to a **toolbox in Microsoft Foundry** via MCP and
serves responses over the Foundry Responses Protocol.

The solution comes with a pre-configured toolbox that includes three tools:

- **Web Search** — Bing-powered web search for real-time information retrieval.
- **Code Interpreter** — Sandboxed Python execution for data analysis, calculations, and code generation.
- **AI Search** — Azure AI Search integration for querying indexed documents with hybrid (keyword + vector) search.

> **Note:** After deployment, the toolbox configuration may need to be verified or adjusted in the
> [Azure AI Foundry portal](https://ai.azure.com). Open your project, navigate to the **Toolboxes**
> section, and confirm that all three tools are correctly configured with the expected connections
> and settings. The portal is the source of truth — if tools appear misconfigured or missing,
> re-create or update them directly there.

## How It Works

1. On startup the agent calls `client.get_tools()` against the Toolbox MCP endpoint.
2. A `create_react_agent` is built from the loaded tools and an Azure OpenAI LLM.
3. Incoming requests are handled by `ResponsesAgentServerHost` on port `8088`.
4. The agent is initialized **lazily** (once, on the first request) and reused for all
   subsequent turns — the MCP client is kept alive to prevent session garbage-collection.
5. When the toolbox requires OAuth consent (e.g. a GitHub connection that hasn't been
   authorized yet), the MCP server returns error code `-32006`. The agent detects this,
   logs the consent URL, and surfaces it to the caller via a fallback tool instead of
   crashing.

## Prerequisites

- Python 3.12+
- A Microsoft Foundry project with a toolbox already created — see
  [`../sample_toolboxes_crud.py`](../sample_toolboxes_crud.py) to create one
- Azure CLI installed and logged in:

  ```bash
  az login
  ```

## Quick Start (Local)

**Linux/macOS:**
```bash
# 1. Copy and fill in the environment file
cp .env.example .env
# Edit .env — set FOUNDRY_PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME,
#              and TOOLBOX_ENDPOINT at minimum

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the agent
python main.py

# 4. Invoke
curl -X POST http://localhost:8088/responses \
  -H "Content-Type: application/json" \
  -d '{"input": "What tools do you have?"}'
```

**Windows (PowerShell):**
```powershell
# 1. Copy and fill in the environment file
Copy-Item .env.example .env
# Edit .env — set FOUNDRY_PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME,
#              and TOOLBOX_ENDPOINT at minimum

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the agent
python main.py

# 4. Invoke
Invoke-RestMethod -Method POST http://localhost:8088/responses `
  -ContentType "application/json" `
  -Body '{"input": "What tools do you have?"}'
```

## Deploy as a Hosted Agent

### Setup

#### 1. Install Azure Developer CLI (`azd`)

**Linux/macOS:**
```bash
curl -fsSL https://aka.ms/install-azd.sh | bash
```

**Windows (PowerShell):**
```powershell
winget install microsoft.azd
```

See the [full installation docs](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) for other options.

#### 2. Install the AI Agents azd extension

```bash
azd extension install azure.ai.agents
```

To upgrade the extension later:

```bash
azd extension upgrade azure.ai.agents
```

#### 3. Log in to Azure

```bash
azd auth login
```

#### 4. Fix git CRLF setting (Windows only)

```bash
git config --global core.autocrlf false
```

### Quick Start (Deploy with azd)

# 1. Set required environment variables
azd env set enableHostedAgentVNext "true" -e my-env
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "gpt-4o" -e my-env  # must match the deployment name in azure.yaml

# 2. Provision infrastructure and deploy the container
azd up -e my-env

# 3. Invoke the deployed agent (run from the scaffolded project directory)
azd ai agent invoke --new-session "What tools do you have?" --timeout 120
```

### Project Structure

After `azd ai agent init`, you get:

```
my-project/
├── .azure/
│   └── <env-name>/
│       ├── .env              # Environment variables (auto-populated by azd)
│       └── config.json       # Subscription + location config
├── infra/
│   ├── main.bicep            # Top-level Bicep template
│   ├── main.parameters.json  # Parameters (references .env values)
│   └── core/ai/
│       ├── ai-project.bicep  # Project + connection deployment
│       └── connection.bicep  # Connection resource template
├── src/
│   └── <agent-name>/
│       ├── agent.yaml        # Agent definition (env vars, protocols)
│       ├── main.py           # Agent code
│       ├── Dockerfile        # Container build
│       └── requirements.txt  # Dependencies
└── azure.yaml                # azd service + toolbox configuration
```

> **Tip:** `azd ai agent invoke` must be run from the scaffolded project directory
> (the directory where `azure.yaml` was created by `azd ai agent init`).  
> The `--timeout 120` flag is recommended — agent cold starts can take up to 60 seconds.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | **Yes** | Project endpoint URL — platform-injected at runtime |
| `MODEL_DEPLOYMENT_NAME` | **Yes** | Model deployment name (e.g. `gpt-4.1`) |
| `TOOLBOX_ENDPOINT` | **Yes** | Full toolbox MCP endpoint URL including toolbox name and api-version |
| `FOUNDRY_AGENT_TOOLBOX_FEATURES` | No | Feature-flag header value — platform-injected (default: `Toolboxes=V1Preview`) |

`TOOLBOX_ENDPOINT` is the full pre-constructed MCP URL. Two forms are supported:
```
# Latest version:
https://<account>.services.ai.azure.com/api/projects/<project>/toolboxes/<name>/mcp?api-version=v1

# Pinned to a specific version:
https://<account>.services.ai.azure.com/api/projects/<project>/toolboxes/<name>/versions/<version>/mcp?api-version=v1
```
The version number is the integer toolbox version (e.g. `1`). Use the versioned form to pin to a known-good version.

## Supported Toolbox Tools

See [../SUPPORTED_TOOLBOX_TOOLS.md](../SUPPORTED_TOOLBOX_TOOLS.md) for all supported
tool and auth types. For runnable SDK creation examples, see
[../sample_toolboxes_crud.py](../sample_toolboxes_crud.py).

## Protocol

This sample uses the **Responses Protocol** (`azure-ai-agentserver-responses`):
- OpenAI-compatible `/responses` endpoint on port `8088`
- Streaming SSE output
- Multi-turn conversation (history fetched automatically)
- 240-second per-request timeout

## Troubleshooting

### Agent starts but returns no tools

Check that `TOOLBOX_ENDPOINT` is set and the toolbox exists. The URL must include
`?api-version=v1`.

### OAuth consent required

If a toolbox connection requires OAuth (e.g. GitHub), the agent logs:
```
OAuth consent required. Open the following URL in a browser to authorize...
```
Open the URL, complete the OAuth flow, then restart the agent. Until consent is granted,
the agent returns a `oauth_consent_required` tool message to callers.

### Tool call failures don't crash the agent

`handle_tool_error = True` is set on all loaded tools, so MCP tool errors are returned
as tool messages rather than raising exceptions that would break conversation state.

### Tool schemas rejected by OpenAI

The agent sanitizes malformed schemas from MCP servers (missing `properties` on
`object`-type schemas). If you see `400 Invalid tool schema` errors, check the raw
tool schema returned by your MCP server.

### `FOUNDRY_PROJECT_ENDPOINT` vs `AZURE_AI_PROJECT_ENDPOINT`

The platform injects `FOUNDRY_PROJECT_ENDPOINT`. The code also accepts
`AZURE_AI_PROJECT_ENDPOINT` for backward compatibility, but always prefer the `FOUNDRY_`
variable in new deployments.

## Tracing

The `azure-ai-agentserver-core[tracing]` package is included in `requirements.txt` and
provides OpenTelemetry auto-instrumentation for LLM calls, MCP tool invocations, and
server spans. Traces can be exported to Azure Monitor (Application Insights).

### Enable Azure Monitor export

**Locally:** set `APPLICATIONINSIGHTS_CONNECTION_STRING` in `.env`:

```bash
# Get the connection string from your Application Insights resource in the Azure Portal
# (Settings → Properties → Connection String)
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=<key>;IngestionEndpoint=...
```

**When deployed:** the platform automatically injects `APPLICATIONINSIGHTS_CONNECTION_STRING`
from the Application Insights resource linked to your Foundry project. No additional
configuration is required.

### View traces in Azure Monitor

Once `APPLICATIONINSIGHTS_CONNECTION_STRING` is set:

1. Go to the [Azure Portal](https://portal.azure.com) and open your Application Insights resource.
2. Navigate to **Investigate** → **Transaction search** to see individual traces.
3. Use **Investigate** → **Application map** for an end-to-end dependency view.

Traces include LLM calls, tool invocations (including MCP calls to the toolbox), and
server spans. Each conversation turn produces a linked trace tree rooted at the
incoming `/responses` request.


## Setting Up Azure AI Search Index (Post-Deployment)

After `azd up` completes, you need to manually perform these steps to create the search index and ingest the sample documents from the `sample-data/` folder.

### Prerequisites

1. **Deploy a `text-embedding-ada-002` model** in your Foundry project (via Azure AI Foundry portal).
2. **Set the AI Search endpoint** in your azd environment (the endpoint is created by the Bicep infrastructure):
   ```bash
   azd env set AZURE_AI_SEARCH_ENDPOINT "https://<your-search-service>.search.windows.net"
   ```

### Create the Index and Ingest Documents

The `ai_search_tools.py` script provides two functions: `create_index` (creates a search index with content + metadata + vector fields) and `ingest_pdfs` (extracts text from PDFs in `sample-data/`, chunks it, generates embeddings, and uploads to the index).

Run the following from the `responses/langgraph-toolbox/` directory:

```bash
# Ensure you're logged in
az login

# Set required environment variables
export AZURE_AI_SEARCH_ENDPOINT="https://<your-search-service>.search.windows.net"
export AZURE_AI_PROJECT_ENDPOINT="<your-foundry-project-endpoint>"
export EMBEDDING_DEPLOYMENT="text-embedding-ada-002"

# Create the index and ingest sample PDFs
python -c "
from ai_search_tools import create_index, ingest_pdfs

# 1. Create the index (includes vector field for embeddings)
result = create_index('insurance-docs')
print(result)

# 2. Ingest PDFs from sample-data/ into the index
result = ingest_pdfs('insurance-docs', './sample-data')
print(result)
"
```

The `sample-data/` folder contains insurance-related PDF documents that will be chunked and indexed with vector embeddings for hybrid search.

### Configuration (Optional)

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `AZURE_AI_SEARCH_ENDPOINT` | — | Azure AI Search service endpoint (required) |
| `AZURE_AI_PROJECT_ENDPOINT` | — | Foundry project endpoint (needed for embedding generation) |
| `EMBEDDING_DEPLOYMENT` | `text-embedding-ada-002` | Embedding model deployment name |
| `PDF_INGEST_FOLDER` | `sample-data/` | Folder containing PDFs to ingest |
| `AI_SEARCH_CHUNK_SIZE` | `2000` | Chunk size in characters |
| `AI_SEARCH_CHUNK_OVERLAP` | `200` | Overlap between chunks |



### Toolbox Configuration

The toolbox is configured in `azure.yaml` and `agent.manifest.yaml`. The manifest declares three tools in the `agent-tools` toolbox: `web_search`, `code_interpreter`, and `azure_ai_search`. You can add or modify toolbox definitions under `config.toolboxes` in `azure.yaml`. See the [azd README](../azd/README.md) for supported toolbox scenarios.

> **Important:** After `azd up`, verify that the toolbox was created correctly in the
> [Azure AI Foundry portal](https://ai.azure.com) under your project's **Toolboxes** section.
> If any tool is missing or misconfigured, update it directly in the portal.

### Monitoring

```powershell
# View container logs
azd ai agent monitor --tail 50
```

### Known Issues

See [../azd/KNOWN_ISSUES.md](../azd/KNOWN_ISSUES.md) for all known issues with azd toolbox deployments.

## Additional Files

- `main.py` — agent entry point (env loading, agent name detection, telemetry, and LangGraph agent)
- `../sample_toolboxes_crud.py` — SDK samples for creating, listing, and deleting toolbox resources

## Contributing

This project welcomes contributions and suggestions.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.

