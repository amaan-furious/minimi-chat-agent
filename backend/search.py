"""
search.py — Web search tool backed by Tavily API.

Tavily returns structured, LLM-optimised results:
  - title, url, content snippet, score
  
We also support a fallback to DuckDuckGo Instant Answers
if Tavily is unavailable or quota is exhausted.
"""

import os
import httpx
from typing import Any

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_URL = "https://api.tavily.com/search"

# Same SSL workaround as agent.py for MSYS64 Python
SSL_VERIFY = os.getenv("SSL_VERIFY", "false").lower() != "false"


async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Search the web. Returns structured results ready for LLM consumption.
    Falls back to DuckDuckGo if Tavily key is missing.
    """
    if TAVILY_API_KEY:
        return await _tavily_search(query, max_results)
    else:
        return await _duckduckgo_search(query, max_results)


async def _tavily_search(query: str, max_results: int) -> dict[str, Any]:
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "include_raw_content": False,
        "max_results": max_results,
    }
    async with httpx.AsyncClient(timeout=15.0, verify=SSL_VERIFY) as client:
        resp = await client.post(TAVILY_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in data.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:600],
            "score": r.get("score", 0),
        })

    return {
        "query": query,
        "answer": data.get("answer", ""),
        "results": results,
        "source": "tavily",
    }


async def _duckduckgo_search(query: str, max_results: int) -> dict[str, Any]:
    """
    DuckDuckGo Instant Answer API (no key required, rate-limited).
    Used as a fallback when Tavily key is not set.
    """
    encoded = httpx.QueryParams(q=query, format="json", no_html=1, skip_disambig=1)
    url = f"https://api.duckduckgo.com/?{encoded}"

    async with httpx.AsyncClient(timeout=10.0, verify=SSL_VERIFY) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in data.get("RelatedTopics", [])[:max_results]:
        if "Text" in r:
            results.append({
                "title": r.get("Text", "")[:80],
                "url": r.get("FirstURL", ""),
                "snippet": r.get("Text", "")[:600],
                "score": 1.0,
            })

    return {
        "query": query,
        "answer": data.get("AbstractText", ""),
        "results": results,
        "source": "duckduckgo",
    }


def format_search_results(search_result: dict[str, Any]) -> str:
    """
    Format search results into a clean string for the LLM.
    """
    lines = []
    if search_result.get("answer"):
        lines.append(f"**Direct Answer:** {search_result['answer']}\n")

    lines.append(f"**Search results for:** `{search_result['query']}`\n")
    for i, r in enumerate(search_result.get("results", []), 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['snippet']}")
        lines.append(f"   Source: {r['url']}\n")

    return "\n".join(lines)
