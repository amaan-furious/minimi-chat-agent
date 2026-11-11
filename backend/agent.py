"""
agent.py — ReAct agent loop using Gemini REST API (via httpx).

Why REST instead of the google-generativeai SDK?
  The SDK pulls in grpcio (a large C extension) which fails to build on
  MSYS64/MinGW Python. Direct REST calls give us full control with zero
  extra dependencies — httpx is already installed.

Architecture:
  1. Load conversation history from memory (with timestamps)
  2. Build system prompt with temporal context
  3. Call Gemini /generateContent with tool definitions
  4. If Gemini calls web_search → execute → feed results back
  5. Stream final response tokens to the caller via async generator

The ReAct pattern (Reason → Act → Observe → Answer) is implemented via
Gemini's native function calling. The model emits a structured function_call
part when it wants to search; we execute the tool and return a function_response
in the next turn.
"""

import json
import os
from datetime import datetime, timezone
from typing import AsyncGenerator, Any, Optional

import httpx

from memory import (
    add_message,
    get_history_for_prompt,
    update_session_title,
    get_session,
)
from prompts import system_prompt, title_generation_prompt
from search import web_search, format_search_results

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Model preference list — first available will be used
MODEL_CANDIDATES = [
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

# Try v1 first (stable), fall back to v1beta
API_VERSIONS = ["v1", "v1beta"]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com"

# Disable SSL verification for MSYS64/MinGW Python (missing system CA bundle).
# In production on a proper Python install, set this to True.
SSL_VERIFY = os.getenv("SSL_VERIFY", "false").lower() != "false"

# Tool definition (Gemini function calling schema)
TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "web_search",
                "description": (
                    "Search the web for current, factual information. "
                    "Use this for recent events, facts, prices, people, places, "
                    "or any question that benefits from up-to-date knowledge."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "A concise, specific search query.",
                        }
                    },
                    "required": ["query"],
                },
            }
        ]
    }
]

GENERATION_CONFIG = {
    "temperature": 0.3,
    "maxOutputTokens": 2048,
}


# ── History formatting ────────────────────────────────────────────────────────

def _build_history_summary(history: list[dict]) -> str:
    """Create a temporal memory summary string for the system prompt."""
    if not history:
        return ""
    lines = ["**Previous conversation (with timestamps):**"]
    for m in history[-20:]:
        ts = m.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            ts_fmt = dt.strftime("%b %d %H:%M UTC")
        except Exception:
            ts_fmt = ts
        role_label = "You" if m["role"] == "user" else "Assistant"
        lines.append(f"- [{ts_fmt}] **{role_label}**: {m['content'][:300]}")
    return "\n".join(lines)


def _history_to_gemini_format(history: list[dict]) -> list[dict]:
    """
    Convert stored history to Gemini's Content format.
    Gemini requires strictly alternating user/model turns.
    Consecutive same-role messages are merged.
    """
    contents = []
    for m in history:
        role = "model" if m["role"] == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            # Merge consecutive same-role messages
            contents[-1]["parts"].append({"text": "\n\n" + m["content"]})
        else:
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
    # Trim trailing model turns — history must end before the new user message
    while contents and contents[-1]["role"] == "model":
        contents.pop()
    return contents


# ── Gemini API calls ──────────────────────────────────────────────────────────

async def _call_gemini(contents: list[dict], system_instruction: str) -> dict:
    """
    Call Gemini /generateContent, trying multiple models and API versions.
    Returns the first successful response.
    """
    payload = {
        "contents": contents,
        "tools": TOOLS,
        "generationConfig": GENERATION_CONFIG,
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
    }
    last_error = None
    async with httpx.AsyncClient(timeout=60.0, verify=SSL_VERIFY) as client:
        for version in API_VERSIONS:
            for model in MODEL_CANDIDATES:
                url = f"{GEMINI_API_BASE}/{version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        return resp.json()
                    elif resp.status_code == 404:
                        continue  # Try next model
                    else:
                        resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    last_error = e
                    continue
    raise last_error or RuntimeError("No working Gemini model found")


async def _call_gemini_simple(prompt: str) -> str:
    """Simple single-turn call with no tools, for title generation etc."""
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 32},
    }
    async with httpx.AsyncClient(timeout=15.0, verify=SSL_VERIFY) as client:
        for version in API_VERSIONS:
            for model in MODEL_CANDIDATES:
                url = f"{GEMINI_API_BASE}/{version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        return _extract_text(resp.json())
                    elif resp.status_code == 404:
                        continue
                    else:
                        resp.raise_for_status()
                except httpx.HTTPStatusError:
                    continue
    return ""


def _extract_text(response: dict) -> str:
    """Pull text from a Gemini response."""
    try:
        parts = response["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        return ""


def _extract_function_call(response: dict) -> Optional[dict]:
    """
    Return the first function call part from a Gemini response, or None.
    Returns the full part dict (including thoughtSignature) so we can
    replay it accurately in the conversation history.
    """
    try:
        parts = response["candidates"][0]["content"]["parts"]
        for part in parts:
            if "functionCall" in part:
                return part  # Return full part including thoughtSignature
    except (KeyError, IndexError):
        pass
    return None


def _get_full_model_parts(response: dict) -> list[dict]:
    """Return all parts from the model response (preserves thoughtSignature)."""
    try:
        return response["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        return []


# ── Main agent entrypoint ─────────────────────────────────────────────────────

async def run_agent(
    session_id: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """
    Core ReAct agent loop. Yields JSON event strings for SSE.

    Events:
      {"type": "token",       "data": "...text chunk..."}
      {"type": "tool_call",   "data": {"tool": "web_search", "args": {...}}}
      {"type": "tool_result", "data": {"query": "...", "result_count": N, "source": "..."}}
      {"type": "done",        "data": ""}
      {"type": "error",       "data": "...error message..."}
    """
    now = datetime.now(timezone.utc).isoformat()

    # 1. Persist user message
    add_message(session_id, "user", user_message)

    # 2. Load history for context
    history = get_history_for_prompt(session_id, limit=40)
    history_without_latest = history[:-1] if history else []

    # 3. Build system prompt with memory
    history_summary = _build_history_summary(history_without_latest)
    sys_prompt = system_prompt(now=now, history_summary=history_summary)

    # 4. Build conversation history in Gemini format
    contents = _history_to_gemini_format(history_without_latest)
    # Append the new user message
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    full_response = ""

    try:
        # ── ReAct loop (max 3 tool call rounds) ──────────────────────────────
        for _round in range(3):
            response = await _call_gemini(contents, sys_prompt)

            # Check for a function call
            fc = _extract_function_call(response)

            if fc:
                fc_data = fc.get("functionCall", {})
                tool_name = fc_data.get("name", "")
                tool_args = fc_data.get("args", {})

                yield _event("tool_call", {"tool": tool_name, "args": tool_args})

                # Execute the tool
                if tool_name == "web_search":
                    query = tool_args.get("query", "")
                    search_result = await web_search(query)
                    formatted = format_search_results(search_result)

                    yield _event("tool_result", {
                        "tool": tool_name,
                        "query": query,
                        "result_count": len(search_result.get("results", [])),
                        "source": search_result.get("source", ""),
                    })

                    # Add the FULL model turn (preserving thoughtSignature)
                    model_parts = _get_full_model_parts(response)
                    contents.append({
                        "role": "model",
                        "parts": model_parts,
                    })
                    # Add tool result turn
                    contents.append({
                        "role": "user",
                        "parts": [{
                            "functionResponse": {
                                "name": tool_name,
                                "response": {"result": formatted},
                            }
                        }],
                    })
                else:
                    # Unknown tool — skip gracefully
                    break

            else:
                # No function call — extract and stream the text response
                text = _extract_text(response)
                if text:
                    full_response = text
                    # Yield in chunks to simulate streaming (Gemini REST doesn't stream)
                    chunk_size = 20
                    for i in range(0, len(text), chunk_size):
                        yield _event("token", text[i:i + chunk_size])
                break

        # ── Persist assistant response ────────────────────────────────────────
        if full_response:
            add_message(session_id, "assistant", full_response)

            # Auto-title the session on first real exchange
            session = get_session(session_id)
            if session and session.get("title") == "New chat":
                await _auto_title(session_id, user_message)

        yield _event("done", "")

    except httpx.HTTPStatusError as e:
        err = f"Gemini API error {e.response.status_code}: {e.response.text[:200]}"
        yield _event("error", err)
    except Exception as e:
        yield _event("error", str(e))


async def _auto_title(session_id: str, first_message: str) -> None:
    """Generate a short title from the first user message."""
    try:
        prompt = title_generation_prompt(first_message)
        title = await _call_gemini_simple(prompt)
        title = title.strip().strip('"').strip("'")
        if title:
            update_session_title(session_id, title[:60])
    except Exception:
        pass  # Best-effort


# ── Helpers ───────────────────────────────────────────────────────────────────

def _event(event_type: str, data: Any) -> str:
    return json.dumps({"type": event_type, "data": data})
