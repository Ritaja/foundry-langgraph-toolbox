"""CRM Agent — a LangGraph ReAct agent specialised in insurance CRM lookups.

Uses DuckDB as the backend for customer, policy, and claims data. The
``crm_analytics`` tool additionally uses an LLM to convert natural-language
questions into SQL for complex analytical queries.
"""

import pathlib
from langgraph.prebuilt import create_react_agent
from agents.crm.tools import CRM_TOOLS, init_crm_tools

_SKILL_PATH = pathlib.Path(__file__).parent / "SKILL.md"


def _load_system_prompt() -> str:
    """Load the CRM system prompt from SKILL.md, stripping YAML front-matter."""
    text = _SKILL_PATH.read_text()
    parts = text.split("---")
    if len(parts) >= 3:
        return "---".join(parts[2:]).strip()
    return text.strip()


CRM_SYSTEM_PROMPT = _load_system_prompt()


def create_crm_agent(llm, extra_tools: list | None = None):
    """Return a compiled LangGraph ReAct agent for CRM queries.

    Args:
        llm: The ChatOpenAI model instance (also used by crm_analytics).
        extra_tools: Optional additional tools (e.g. from Foundry toolbox MCP)
                     to make available alongside the CRM-specific tools.
    """
    # Provide the LLM to the analytics tool so it can generate SQL
    init_crm_tools(llm)

    all_tools = CRM_TOOLS + (extra_tools or [])
    return create_react_agent(llm, all_tools, prompt=CRM_SYSTEM_PROMPT)
