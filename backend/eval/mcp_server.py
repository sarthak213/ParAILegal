"""MCP server exposing ParAILegal retrieval as a `search_law` tool, for ToolEval's end-to-end runs.

    python -m eval.mcp_server        (ToolEval starts it from the pack's dataset.json)

PARAILEGAL_SEARCH_URL selects the retriever: a running v1 API (default http://127.0.0.1:8000).
The v2 offline retriever will plug in here in-process once it exists.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

from app.infrastructure.llm.answerer import _build_context

SEARCH_URL = os.environ.get("PARAILEGAL_SEARCH_URL", "http://127.0.0.1:8000")
TOP_K = int(os.environ.get("PARAILEGAL_TOP_K", "5"))

mcp = FastMCP("parailegal")


@mcp.tool()
def search_law(query: str, domain: str | None = None) -> str:
    """Search Indian law and return the most relevant provisions with their citations."""
    payload: dict = {"query": query, "k": TOP_K}
    if domain in ("constitution", "statutes", "judgements", "all"):
        payload["domain"] = domain
    resp = httpx.post(f"{SEARCH_URL}/api/v1/search", json=payload, timeout=120)
    resp.raise_for_status()
    results = resp.json()["results"]
    if not results:
        return "No provisions found."
    return _build_context(results).strip()


if __name__ == "__main__":
    mcp.run()
