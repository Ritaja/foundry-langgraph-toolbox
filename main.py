"""LangGraph agent on Azure AI Foundry with Foundry Toolbox.

A ReAct agent that connects to the Foundry toolbox for all tools:
- Fabric Data Agent (via MCP, agent-identity auth managed by platform)
- Web Search (platform-managed)
- Code Interpreter (platform-managed)

## Platform-Injected Environment Variables (container-image-spec)

The Foundry platform injects these at runtime:
- `FOUNDRY_PROJECT_ENDPOINT` — project endpoint
- `FOUNDRY_AGENT_TOOLBOX_ENDPOINT` — base URL for toolbox MCP proxy
- `FOUNDRY_AGENT_TOOLBOX_FEATURES` — feature-flag headers for toolbox requests

## User-Defined Variables

- `AZURE_AI_MODEL_DEPLOYMENT_NAME` — chat model deployment name
- `TOOLBOX_ENDPOINT` — full toolbox MCP endpoint URL (optional override)
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

# ── Langfuse observability (optional) ───────────────────────────────────────
# Initialise before any LangChain imports so the singleton is available.

_langfuse_enabled = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)

if _langfuse_enabled:
    try:
        from langfuse import Langfuse, get_client
        from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

        Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            base_url=os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            debug=True,
        )
    except Exception as _lf_err:
        logging.getLogger(__name__).warning(f"Langfuse init failed: {_lf_err} — tracing disabled")
        _langfuse_enabled = False

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

if _langfuse_enabled:
    logger.info("Langfuse tracing enabled")

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

# ── Agent creation ──────────────────────────────────────────────────────────


async def quickstart():
    """Build the ReAct agent graph with toolbox MCP tools."""
    all_mcp_tools: list = []
    mcp_clients: list = []

    # Foundry Toolbox MCP (Fabric Data Agent + web search + code interpreter)
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
        logger.info(f"Total tools loaded: {len(all_mcp_tools)}")
    else:
        logger.warning("No tools loaded — agent will operate without tools")

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

        logger.info(f"Invoking agent with {len(lc_messages)} messages")
        invoke_config: dict = {}
        if _langfuse_enabled:
            invoke_config["callbacks"] = [LangfuseCallbackHandler()]
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": lc_messages}, config=invoke_config),
            timeout=240.0,
        )
        logger.info(f"Agent returned {len(result.get('messages', []))} messages")
        assistant_reply = _extract_assistant_text(result)
        if not assistant_reply:
            assistant_reply = "(Agent completed without text response)"
    except asyncio.TimeoutError:
        assistant_reply = "I could not complete this request within the local timeout. Please retry with a simpler prompt."
    except asyncio.CancelledError:
        assistant_reply = "The request was cancelled before completion. Please retry."
    except Exception as e:
        logger.exception(f"Agent invocation failed: {e}")
        assistant_reply = f"An error occurred: {e}"
    finally:
        if _langfuse_enabled:
            try:
                get_client().flush()
            except Exception:
                pass

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
