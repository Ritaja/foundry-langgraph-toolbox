"""LangGraph agent on Azure AI Foundry with Fabric Data Agent MCP.

A ReAct agent that connects to a Microsoft Fabric Data Agent directly via
its MCP endpoint (with app-managed auth), and to the Foundry toolbox for
platform-managed tools (web search, code interpreter).

When the Fabric Data Agent cannot execute data queries (e.g. managed-identity
callers hit OBO limitations), a direct DAX query fallback tool is available
that calls the Power BI REST API with the agent's own token.

## Platform-Injected Environment Variables (container-image-spec)

The Foundry platform injects these at runtime:
- `FOUNDRY_PROJECT_ENDPOINT` — project endpoint
- `FOUNDRY_AGENT_TOOLBOX_ENDPOINT` — base URL for toolbox MCP proxy
- `FOUNDRY_AGENT_TOOLBOX_FEATURES` — feature-flag headers for toolbox requests

## User-Defined Variables

- `AZURE_AI_MODEL_DEPLOYMENT_NAME` — chat model deployment name
- `FABRIC_MCP_ENDPOINT` — Fabric Data Agent MCP endpoint URL
- `TOOLBOX_ENDPOINT` — full toolbox MCP endpoint URL (optional override)
- `FABRIC_WORKSPACE_ID` — Fabric workspace ID for direct DAX queries
- `FABRIC_DATASET_ID` — Semantic model (dataset) ID for direct DAX queries
"""

import asyncio
import json
import logging
import os
import pathlib
import re

import httpx
from dotenv import load_dotenv

load_dotenv(override=False)

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage
from azure.ai.agentserver.responses import (
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    get_input_expanded,
)
from azure.ai.agentserver.responses.models import (
    CreateResponse,
    MessageContentInputTextContent,
    MessageContentOutputTextContent,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_mcp_adapters.client import MultiServerMCPClient

from orchestrator import build_orchestrator_graph

# ── Agent name and logger ────────────────────────────────────────────────────


def _read_agent_name() -> str:
    try:
        yaml_text = pathlib.Path("agent.yaml").read_text()
        m = re.search(r"^name:\s*(.+)$", yaml_text, re.MULTILINE)
        return m.group(1).strip() if m else "unknown-agent"
    except Exception:
        return "unknown-agent"


AGENT_NAME = _read_agent_name()
logger = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

# ── LLM (Chat Completions API via Azure OpenAI endpoint) ────────────────────

PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
if not PROJECT_ENDPOINT:
    raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be set")

MODEL_DEPLOYMENT_NAME = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "")
if not MODEL_DEPLOYMENT_NAME:
    raise ValueError("AZURE_AI_MODEL_DEPLOYMENT_NAME environment variable must be set")

_credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(
    _credential,
    "https://ai.azure.com/.default",
)


class _AzureTokenAuth(httpx.Auth):
    """httpx Auth that injects a fresh bearer token on every request."""

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {token_provider()}"
        yield request


_llm_http_client = httpx.Client(auth=_AzureTokenAuth())
_llm_async_http_client = httpx.AsyncClient(auth=_AzureTokenAuth())

llm = ChatOpenAI(
    base_url=f"{PROJECT_ENDPOINT.rstrip('/')}/openai/v1",
    api_key="placeholder",  # overridden by _AzureTokenAuth
    model=MODEL_DEPLOYMENT_NAME,
    http_client=_llm_http_client,
    http_async_client=_llm_async_http_client,
)

# ── Toolbox MCP helpers ────────────────────────────────────────────────────

_TOOLBOX_NAME = os.getenv("TOOLBOX_NAME", "")
TOOLBOX_ENDPOINT = (
    f"{PROJECT_ENDPOINT.rstrip('/')}/toolboxes/{_TOOLBOX_NAME}/mcp?api-version=v1"
    if _TOOLBOX_NAME
    else os.getenv("TOOLBOX_ENDPOINT", "")
)
_TOOLBOX_FEATURES = os.getenv("FOUNDRY_AGENT_TOOLBOX_FEATURES", "Toolboxes=V1Preview")

# ── Fabric Data Agent MCP endpoint ──────────────────────────────────────────

FABRIC_MCP_ENDPOINT = os.getenv(
    "FABRIC_MCP_ENDPOINT",
    "https://api.fabric.microsoft.com/v1/mcp/workspaces/71308ecc-8e37-44e5-b047-148f1af540f4/dataagents/fbf7af71-7fca-42d2-8982-db4925eddc62/agent",
)

# Direct DAX query settings (fallback when Fabric Data Agent can't execute data queries)
FABRIC_WORKSPACE_ID = os.getenv(
    "FABRIC_WORKSPACE_ID",
    "71308ecc-8e37-44e5-b047-148f1af540f4",
)
FABRIC_DATASET_ID = os.getenv(
    "FABRIC_DATASET_ID",
    "37947b9f-4de7-4ae0-9f32-c812efc55564",
)
FABRIC_LAKEHOUSE_ID = os.getenv(
    "FABRIC_LAKEHOUSE_ID",
    "c2bf5944-c092-4541-a6d7-4a3838a39fbc",
)

# ── Agent creation ──────────────────────────────────────────────────────────


class _FabricTokenAuth(httpx.Auth):
    """httpx Auth that injects a Fabric-scoped bearer token."""

    def __init__(self):
        self._credential = DefaultAzureCredential()
        self._token_provider = get_bearer_token_provider(
            self._credential,
            "https://api.fabric.microsoft.com/.default",
        )

    def auth_flow(self, request):
        token = self._token_provider()
        request.headers["Authorization"] = f"Bearer {token}"
        yield request



# ── Direct Data Query Tools (reads delta tables from OneLake) ────────────────

def _build_data_tools() -> list:
    """Build LangChain tools that read data directly from OneLake delta tables.

    The Power BI executeQueries API doesn't support managed identity tokens,
    and the Fabric Data Agent's internal OBO flow also fails with managed
    identities. These tools bypass both by reading delta/parquet files
    directly from OneLake using a storage-scoped token.
    """
    from langchain_core.tools import tool as lc_tool

    _storage_credential = DefaultAzureCredential()
    _onelake_base = (
        f"abfss://{FABRIC_WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com"
        f"/{FABRIC_LAKEHOUSE_ID}/Tables"
    )

    def _read_delta_table(table_name: str) -> "pandas.DataFrame":
        """Read a delta table from OneLake and return as a pandas DataFrame."""
        import deltalake
        import pandas

        table_path = f"{_onelake_base}/{table_name}"
        storage_options = {
            "azure_storage_account_name": "onelake",
            "azure_use_fabric_endpoint": "true",
        }
        try:
            dt = deltalake.DeltaTable(
                table_path,
                storage_options=storage_options,
            )
            return dt.to_pandas()
        except Exception:
            # Fallback: try with explicit token
            token = _storage_credential.get_token("https://storage.azure.com/.default")
            storage_options["azure_storage_token"] = token.token
            dt = deltalake.DeltaTable(
                table_path,
                storage_options=storage_options,
            )
            return dt.to_pandas()

    @lc_tool
    def get_data_schema() -> str:
        """Get the schema of all tables in the InsuranceGold lakehouse.
        Call this FIRST before querying any data so you know the exact table
        and column names. Returns table names and their columns with data types."""
        import httpx as _httpx

        fabric_tp = get_bearer_token_provider(_storage_credential, "https://api.fabric.microsoft.com/.default")
        url = f"https://api.fabric.microsoft.com/v1/workspaces/{FABRIC_WORKSPACE_ID}/lakehouses/{FABRIC_LAKEHOUSE_ID}/tables"
        try:
            resp = _httpx.get(url, headers={"Authorization": f"Bearer {fabric_tp()}"}, timeout=30)
            resp.raise_for_status()
            tables_data = resp.json().get("data", [])
            if not tables_data:
                return "No tables found in the lakehouse."

            lines = ["# InsuranceGold Lakehouse Tables\n"]
            for tbl in tables_data:
                tname = tbl.get("name", "unknown")
                lines.append(f"## Table: '{tname}'")
                # Read schema from delta table
                try:
                    df = _read_delta_table(tname)
                    for col in df.columns:
                        dtype = str(df[col].dtype)
                        lines.append(f"  - {col} ({dtype})")
                    lines.append(f"  ({len(df)} rows)")
                except Exception as e:
                    lines.append(f"  (schema unavailable: {e})")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"Schema query failed: {type(e).__name__}: {e}"

    @lc_tool
    def query_insurance_data(table_name: str, filter_column: str = "", filter_value: str = "", columns: str = "", limit: int = 50) -> str:
        """Query data from a specific table in the InsuranceGold lakehouse.
        Call get_data_schema first to learn table and column names.

        Args:
            table_name: Name of the table to query (e.g. 'insurance_product_type')
            filter_column: Optional column name to filter on
            filter_value: Value to filter for (exact match)
            columns: Comma-separated column names to return (empty = all columns)
            limit: Maximum rows to return (default 50)
        """
        try:
            df = _read_delta_table(table_name)

            if columns:
                col_list = [c.strip() for c in columns.split(",")]
                valid_cols = [c for c in col_list if c in df.columns]
                if valid_cols:
                    df = df[valid_cols]

            if filter_column and filter_value and filter_column in df.columns:
                df = df[df[filter_column].astype(str) == str(filter_value)]

            df = df.head(limit)

            if df.empty:
                return f"No data found in '{table_name}' with the given filters."

            return df.to_markdown(index=False)
        except Exception as e:
            return f"Query failed: {type(e).__name__}: {e}"

    @lc_tool
    def analyze_insurance_data(question: str) -> str:
        """Analyze insurance data by loading relevant tables and computing results.
        Use this for complex analytical questions like comparing ratios, aggregating
        data across tables, or computing statistics. Describe your question in
        natural language.

        Args:
            question: Natural language question about the insurance data
        """
        import pandas as pd

        try:
            # Load all tables into a dict for analysis
            import httpx as _httpx

            fabric_tp = get_bearer_token_provider(_storage_credential, "https://api.fabric.microsoft.com/.default")
            url = f"https://api.fabric.microsoft.com/v1/workspaces/{FABRIC_WORKSPACE_ID}/lakehouses/{FABRIC_LAKEHOUSE_ID}/tables"
            resp = _httpx.get(url, headers={"Authorization": f"Bearer {fabric_tp()}"}, timeout=30)
            resp.raise_for_status()
            table_names = [t["name"] for t in resp.json().get("data", [])]

            tables: dict[str, pd.DataFrame] = {}
            for tname in table_names:
                try:
                    tables[tname] = _read_delta_table(tname)
                except Exception as e:
                    logger.warning(f"Could not load table {tname}: {e}")

            # Build a summary for the LLM to use
            result_parts = [f"Loaded {len(tables)} tables: {list(tables.keys())}\n"]

            # For questions about product types, loss/expense ratios
            if "insurance_product_type" in tables:
                ipt = tables["insurance_product_type"]
                result_parts.append(f"## insurance_product_type ({len(ipt)} rows)")
                result_parts.append(ipt.to_markdown(index=False))
                result_parts.append("")

            # For questions about claims
            if "claims" in tables and ("claim" in question.lower() or "loss" in question.lower()):
                claims = tables["claims"]
                result_parts.append(f"## claims ({len(claims)} rows, showing first 20)")
                result_parts.append(claims.head(20).to_markdown(index=False))
                result_parts.append("")

            # For questions about agents/sales
            if "agent_sales" in tables and ("agent" in question.lower() or "sale" in question.lower()):
                sales = tables["agent_sales"]
                result_parts.append(f"## agent_sales ({len(sales)} rows, showing first 20)")
                result_parts.append(sales.head(20).to_markdown(index=False))
                result_parts.append("")

            # For questions about commissions
            if "agent_commission" in tables and "commission" in question.lower():
                comm = tables["agent_commission"]
                result_parts.append(f"## agent_commission ({len(comm)} rows, showing first 20)")
                result_parts.append(comm.head(20).to_markdown(index=False))
                result_parts.append("")

            return "\n".join(result_parts)
        except Exception as e:
            return f"Analysis failed: {type(e).__name__}: {e}"

    return [get_data_schema, query_insurance_data, analyze_insurance_data]


async def quickstart():
    """Build the ReAct agent graph with Fabric MCP + toolbox MCP + OneLake tools."""
    all_mcp_tools: list = []
    mcp_clients: list = []

    # Connect to each MCP endpoint independently so one failure
    # doesn't prevent the other tools from loading.

    # Fabric Data Agent MCP (direct connection with app-managed auth)
    if FABRIC_MCP_ENDPOINT:
        logger.info(f"Connecting to Fabric Data Agent MCP: {FABRIC_MCP_ENDPOINT}")
        try:
            fabric_auth = _FabricTokenAuth()
            fabric_client = MultiServerMCPClient(
                {
                    "fabric_data_agent": {
                        "url": FABRIC_MCP_ENDPOINT,
                        "transport": "streamable_http",
                        "auth": fabric_auth,
                    }
                }
            )
            fabric_tools = await fabric_client.get_tools()
            for t in fabric_tools:
                t.handle_tool_error = True
            all_mcp_tools.extend(fabric_tools)
            mcp_clients.append(fabric_client)
            logger.info(f"Loaded {len(fabric_tools)} Fabric tools: {[t.name for t in fabric_tools]}")
        except Exception as e:
            logger.warning(f"Failed to connect to Fabric Data Agent MCP: {e}")

    # Foundry Toolbox MCP (platform-managed: web search, code interpreter)
    if TOOLBOX_ENDPOINT:
        logger.info(f"Connecting to toolbox: {TOOLBOX_ENDPOINT}")
        try:
            extra_headers = {"Foundry-Features": _TOOLBOX_FEATURES} if _TOOLBOX_FEATURES else {}
            toolbox_client = MultiServerMCPClient(
                {
                    "toolbox": {
                        "url": TOOLBOX_ENDPOINT,
                        "transport": "streamable_http",
                        "headers": extra_headers,
                        "auth": _AzureTokenAuth(),
                    }
                }
            )
            toolbox_tools = await toolbox_client.get_tools()
            for t in toolbox_tools:
                t.handle_tool_error = True
            all_mcp_tools.extend(toolbox_tools)
            mcp_clients.append(toolbox_client)
            logger.info(f"Loaded {len(toolbox_tools)} toolbox tools: {[t.name for t in toolbox_tools]}")
        except Exception as e:
            logger.warning(f"Failed to connect to toolbox MCP: {e} — continuing without toolbox tools")

    if all_mcp_tools:
        logger.info(f"Total MCP tools loaded: {len(all_mcp_tools)}")
    else:
        logger.warning("No MCP tools loaded — agent will operate without MCP tools")

    # Always add direct OneLake data query tools as reliable data access path.
    # The Power BI executeQueries API doesn't accept managed identity tokens,
    # and the Fabric Data Agent MCP fails with OBO for managed identities.
    # These tools read delta tables directly from OneLake via storage API.
    try:
        data_tools = _build_data_tools()
        all_mcp_tools.extend(data_tools)
        logger.info(f"Added {len(data_tools)} data query tools: {[t.name for t in data_tools]}")
    except Exception as e:
        logger.warning(f"Failed to build data query tools: {e}")

    graph = build_orchestrator_graph(llm, mcp_tools=all_mcp_tools)
    logger.info("Agent ready")
    return graph, mcp_clients


def _extract_assistant_text(result: dict) -> str:
    """Best-effort extraction of assistant text from a LangGraph response."""
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", "")
        if msg_type != "ai":
            continue

        content = getattr(msg, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "\n".join(parts)
    return ""


def _get_input_text(request: CreateResponse) -> str | None:
    """Extract plain text from a CreateResponse input."""
    inp = request.input
    if isinstance(inp, str):
        return inp
    items = get_input_expanded(request)
    for item in items:
        content = getattr(item, "content", None)
        if content is None:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if text:
                    return text
    return None


def _history_to_langchain_messages(history: list) -> list:
    """Convert responses-protocol history items to LangChain messages."""
    messages = []
    for item in history:
        if hasattr(item, "content") and item.content:
            for content in item.content:
                if isinstance(content, MessageContentOutputTextContent) and content.text:
                    messages.append(AIMessage(content=content.text))
                elif isinstance(content, MessageContentInputTextContent) and content.text:
                    messages.append(HumanMessage(content=content.text))
    return messages


server = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20),
)

_agent = None
_mcp_clients = None  # prevent MCP session GC
_agent_lock = asyncio.Lock()


async def _get_agent():
    global _agent, _mcp_clients
    if _agent is not None:
        return _agent

    async with _agent_lock:
        if _agent is not None:
            return _agent

        _agent, _mcp_clients = await quickstart()
        return _agent


@server.response_handler
async def handle_response(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    stream = ResponseEventStream(
        response_id=context.response_id,
        model=getattr(request, "model", None),
    )

    yield stream.emit_created()
    yield stream.emit_in_progress()

    user_input = _get_input_text(request) or ""
    if not user_input:
        message_item = stream.add_output_item_message()
        yield message_item.emit_added()
        for event in message_item.text_content("No input provided."):
            yield event
        yield message_item.emit_done()
        yield stream.emit_completed()
        return

    try:
        agent = await _get_agent()

        # Build message list with conversation history for multi-turn context
        try:
            history = await context.get_history()
        except Exception:
            history = []
        lc_messages = _history_to_langchain_messages(history)
        lc_messages.append(HumanMessage(content=user_input))

        result = await asyncio.wait_for(
            agent.ainvoke({"messages": lc_messages}),
            timeout=240.0,
        )
        assistant_reply = _extract_assistant_text(result)
        if not assistant_reply:
            assistant_reply = "(Agent completed without text response)"
    except asyncio.TimeoutError:
        assistant_reply = "I could not complete this request within the local timeout. Please retry with a simpler prompt."
    except asyncio.CancelledError:
        assistant_reply = "The request was cancelled before completion. Please retry."

    message_item = stream.add_output_item_message()
    yield message_item.emit_added()

    text_content = message_item.add_text_content()
    yield text_content.emit_added()
    yield text_content.emit_delta(assistant_reply)
    yield text_content.emit_text_done()
    yield text_content.emit_done()
    yield message_item.emit_done()

    yield stream.emit_completed()


if __name__ == "__main__":
    server.run()
