"""Tavily web search (OpenReef feature 2, fully optional).

Agents and the report agent can search the live web while working.
Enabled only when TAVILY_API_KEY is set; every entry point degrades to a
clear no-op message otherwise, so existing MiroFish setups are unaffected.

Tavily is a plain HTTPS API — no extra containers, works identically in
Docker and Apple containers. Get a key at https://app.tavily.com
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_MAX_RESULTS = 5
MAX_MAX_RESULTS = 10  # bound credit burn per call
REQUEST_TIMEOUT_SECONDS = 20.0


def is_web_search_configured() -> bool:
    """True when TAVILY_API_KEY is present in the environment."""
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def _post_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = client.post(TAVILY_SEARCH_URL, json=payload)
        response.raise_for_status()
        return response.json()


def format_results(results: List[Dict[str, Any]]) -> str:
    """Compact, quotable digest of Tavily results for LLM context."""
    if not results:
        return "No web results found."
    lines = []
    for index, item in enumerate(results, 1):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        snippet = " ".join((item.get("content") or "").split())[:500]
        lines.append(f"{index}. {title}\n   {url}\n   {snippet}")
    return "\n\n".join(lines)


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """Search the live web via Tavily and return a compact text digest.

    This exact callable is also wrapped as a camel FunctionTool for OASIS
    simulation agents — keep the signature simple and the docstring
    agent-facing (it becomes the tool description).
    """
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return "Web search is not configured (TAVILY_API_KEY missing)."
    if not query or not query.strip():
        return "Empty search query."

    try:
        max_results = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), MAX_MAX_RESULTS))
    except (TypeError, ValueError):
        max_results = DEFAULT_MAX_RESULTS

    payload = {
        "api_key": api_key,
        "query": query.strip(),
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    try:
        data = _post_search(payload)
    except Exception as error:
        return f"Web search failed: {type(error).__name__}: {error}"

    return format_results(data.get("results") or [])
