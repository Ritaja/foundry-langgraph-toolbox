"""Supervisor orchestrator using LangGraph.

A single ReAct agent that has access to ALL tools (CRM tools + toolbox MCP
tools).  The combined system prompt gives it domain knowledge and skill
instructions so it can handle multi-step, multi-tool requests in a single
turn — e.g. "look up John Doe's policies and then search the web for the
Zurich Motor Policy terms".

Conversation history is passed in via the messages list so the agent
maintains context across turns.
"""

from __future__ import annotations

import logging
import pathlib

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from agents.crm.agent import CRM_SYSTEM_PROMPT
from agents.crm.tools import CRM_TOOLS, init_crm_tools
from tools.web_search import web_search

logger = logging.getLogger(__name__)

# ── System prompt ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT_PATH = pathlib.Path(__file__).parent / "SYSTEM_PROMPT.md"
_BASE_PROMPT = _SYSTEM_PROMPT_PATH.read_text().strip()

_COMBINED_PROMPT = f"""{_BASE_PROMPT}

## CRM Skill Instructions

{CRM_SYSTEM_PROMPT}
"""


# ── Graph builder ───────────────────────────────────────────────────────────


def build_orchestrator_graph(llm: ChatOpenAI, toolbox_tools: list | None = None):
    """Return a compiled LangGraph ReAct agent with CRM + toolbox tools.

    Args:
        llm: The ChatOpenAI model instance.
        toolbox_tools: Optional list of tools loaded from the Foundry toolbox MCP.
    """
    # Provide the LLM to the CRM analytics tool so it can generate SQL
    init_crm_tools(llm)

    # Filter out the MCP web_search tool (broken 404) — use our native one instead
    filtered_toolbox = [t for t in (toolbox_tools or []) if t.name != "web_search"]
    all_tools = list(CRM_TOOLS) + [web_search] + filtered_toolbox
    tool_info = [(t.name, getattr(t, 'description', '')[:80]) for t in all_tools]
    logger.info(f"ReAct agent tools ({len(all_tools)}): {tool_info}")
    return create_react_agent(llm, all_tools, prompt=_COMBINED_PROMPT)
