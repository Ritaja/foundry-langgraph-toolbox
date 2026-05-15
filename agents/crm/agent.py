"""CRM Agent — a LangGraph ReAct agent specialised in insurance CRM lookups."""

import pathlib
from langgraph.prebuilt import create_react_agent
from agents.crm.tools import CRM_TOOLS

_SKILL_PATH = pathlib.Path(__file__).parent / "SKILL.md"


def _load_system_prompt() -> str:
    """Load the CRM system prompt from SKILL.md, stripping YAML front-matter."""
    text = _SKILL_PATH.read_text()
    # Strip optional YAML front-matter delimited by ---
    parts = text.split("---")
    if len(parts) >= 3:
        # Everything after the second '---'
        return "---".join(parts[2:]).strip()
    return text.strip()


CRM_SYSTEM_PROMPT = _load_system_prompt()


def create_crm_agent(llm, extra_tools: list | None = None):
    """Return a compiled LangGraph ReAct agent for CRM queries.

    Args:
        llm: The ChatOpenAI model instance.
        extra_tools: Optional additional tools (e.g. from Foundry toolbox MCP)
                     to make available alongside the CRM-specific tools.
    """
    all_tools = CRM_TOOLS + (extra_tools or [])
    return create_react_agent(llm, all_tools, prompt=CRM_SYSTEM_PROMPT)
