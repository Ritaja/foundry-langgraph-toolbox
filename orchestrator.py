"""Orchestrator using LangGraph.

A single ReAct agent that has access to Fabric Data Agent tools (loaded via
MCP) and optional Foundry toolbox tools.  The system prompt gives the agent
context about what tools are available so it can answer data questions by
delegating to the Fabric Data Agent.

Conversation history is passed in via the messages list so the agent
maintains context across turns.
"""

from __future__ import annotations

import logging
import pathlib

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

logger = logging.getLogger(__name__)

# ── System prompt ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT_PATH = pathlib.Path(__file__).parent / "SYSTEM_PROMPT.md"
_SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text().strip()


# ── Graph builder ───────────────────────────────────────────────────────────


def build_orchestrator_graph(llm: ChatOpenAI, mcp_tools: list | None = None):
    """Return a compiled LangGraph ReAct agent with toolbox MCP tools.

    All tools (Fabric Data Agent, web search, code interpreter) are provided
    by the Foundry toolbox via MCP — no local tool definitions needed.

    Args:
        llm: The ChatOpenAI model instance.
        mcp_tools: Tools loaded from the Foundry toolbox MCP endpoint.
    """
    all_tools = list(mcp_tools or [])
    tool_info = [(t.name, getattr(t, 'description', '')[:80]) for t in all_tools]
    logger.info(f"ReAct agent tools ({len(all_tools)}): {tool_info}")
    return create_react_agent(llm, all_tools, prompt=_SYSTEM_PROMPT)
