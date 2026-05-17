"""Web search tool using Azure OpenAI Responses API with web_search_preview.

Calls the Responses API directly with web_search_preview to ground results
in real-time web data, bypassing the toolbox MCP (which currently has a
deployment-path bug causing 404s).
"""

from __future__ import annotations

import logging
import os

import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_credential = DefaultAzureCredential()
_token_provider = get_bearer_token_provider(_credential, "https://cognitiveservices.azure.com/.default")

_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").rstrip("/")
_MODEL = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "")


@tool
async def web_search(query: str) -> str:
    """Search the web for current information using Bing.

    Use this tool whenever the user asks for external, real-time, or factual
    information that is not available in the Fabric data — for example,
    documentation, product announcements, regulatory updates, weather
    events, or news.

    Args:
        query: The search query to execute.
    """
    if not _PROJECT_ENDPOINT or not _MODEL:
        return "Error: web search is not configured (missing PROJECT_ENDPOINT or MODEL)."

    # Use the account-level endpoint (derived from project endpoint)
    # Project endpoint: .../api/projects/{project}
    # Account endpoint: just the base host
    base = _PROJECT_ENDPOINT.split("/api/projects/")[0] if "/api/projects/" in _PROJECT_ENDPOINT else _PROJECT_ENDPOINT
    url = f"{base}/openai/responses?api-version=2025-03-01-preview"

    token = _token_provider()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _MODEL,
        "input": query,
        "tools": [{"type": "web_search_preview"}],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Extract text from the response output
        output = data.get("output", [])
        parts = []
        for item in output:
            if item.get("type") == "web_search_call":
                continue
            if item.get("type") == "message":
                for content in item.get("content", []):
                    text = content.get("text", "")
                    if text:
                        parts.append(text)
        return "\n".join(parts) if parts else "No results found."

    except httpx.HTTPStatusError as e:
        logger.error(f"Web search HTTP error: {e.response.status_code} - {e.response.text[:200]}")
        return f"Web search failed: HTTP {e.response.status_code}"
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return f"Web search failed: {e}"
