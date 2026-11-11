# Design: minimi Chat Agent

> A ReAct agent with real-time web search, streaming responses, and timestamp-aware persistent memory.

---

## The goal

Build the best-architected chat agent with one tool — web search. It must:
- Answer fast and accurately
- Remember what you asked and when
- Make smart decisions about *when* to search vs. when to answer directly

---

## Architecture

```
Browser (SSE)
     │
     ▼
FastAPI (main.py)
     │  POST /chat → EventSourceResponse
     ▼
Agent Loop (agent.py)   ←── Gemini 1.5 Flash + Function Calling
     │
     ├─→ web_search(query)      [search.py]
     │       Tavily API (primary)
     │       DuckDuckGo (fallback, no key needed)
     │
     └─→ Memory (memory.py)
             SQLite: sessions + messages + timestamps
```

---

## Key design decisions

### 1. ReAct via native function calling (not prompt-engineered)

Most toy agents simulate ReAct by parsing `Thought:` / `Action:` strings from raw text. This breaks easily.

I use **Gemini's native function calling** via direct REST API calls (httpx) instead of the `google-generativeai` SDK. The SDK pulls in `grpcio` — a large C extension — which is unnecessary for a web app that only needs HTTP. Direct REST gives us:
- Zero extra dependencies (httpx is already in the stack)
- Full control over the request/response cycle
- Easier debugging (plain JSON in, JSON out)

The model emits a structured `functionCall` part when it wants to search, and receives a `functionResponse` part back. This is:
- More reliable (no regex parsing)
- Easier to extend (add more tools later)
- Cleaner separation between reasoning and action

The loop runs up to 3 rounds of tool use, then forces a final text answer.

### 2. Streaming via Server-Sent Events (SSE)

The backend streams typed events over SSE:

```
{"type": "tool_call",   "data": {"tool": "web_search", "args": {...}}}
{"type": "tool_result", "data": {"query": "...", "result_count": 5}}
{"type": "token",       "data": "The latest..."}
{"type": "done",        "data": ""}
```

The frontend renders these progressively:
- Tool calls → show amber spinner with query
- Tool results → flip to green checkmark
- Tokens → appended character by character with blinking cursor
- Done → re-render bubble as full markdown

This makes the agent feel **alive** — you watch it think.

### 3. Timestamp-aware memory

Every message is stored in SQLite with an ISO-8601 UTC timestamp. The system prompt includes the last 20 messages with their timestamps:

```
[Nov 10 09:14 UTC] User: What's the best LLM for coding?
[Nov 10 09:14 UTC] Assistant: As of today, Claude 3.5 Sonnet leads...
```

The model can then naturally reference "yesterday you asked about X" or "earlier we discussed Y" — no special summarisation pipeline needed for short-to-medium histories.

For long histories, a separate summarisation pass condenses older context into bullet points, keeping the prompt size bounded.

### 4. Tavily for search, DuckDuckGo as fallback

[Tavily](https://tavily.com) is purpose-built for LLM agents:
- Returns clean, pre-summarised snippets (no HTML to strip)
- Includes a direct `answer` field for simple factual queries
- Scores results by relevance

DuckDuckGo Instant Answers is the zero-config fallback — no API key needed, so the agent works out of the box.

### 5. Auto-titling sessions

After the first assistant response, Gemini generates a 4-6 word title for the session. This runs asynchronously and doesn't block the response.

### 6. No LangChain

Deliberately avoided. LangChain adds abstraction weight for minimal benefit at this scale. The agent loop is ~80 lines of explicit Python — readable, debuggable, and easy to extend.

---

## Project structure

```
minimi_assessment/
├── backend/
│   ├── main.py        — FastAPI app, routes, SSE endpoint
│   ├── agent.py       — ReAct loop, Gemini integration, streaming
│   ├── memory.py      — SQLite: sessions, messages, timestamps
│   ├── search.py      — Tavily + DuckDuckGo web search
│   ├── prompts.py     — All LLM prompts (isolated for iteration)
│   └── requirements.txt
├── frontend/
│   ├── index.html     — Semantic HTML shell
│   ├── style.css      — Vanilla CSS dark-mode UI
│   └── app.js         — SSE client, markdown renderer, state
├── design.md          — This file
├── README.md
└── .env.example
```

---

## Data model

```sql
sessions (
  id         TEXT PRIMARY KEY,   -- UUID
  title      TEXT,
  created_at TEXT,               -- ISO-8601 UTC
  updated_at TEXT
)

messages (
  id         INTEGER PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id),
  role       TEXT,               -- 'user' | 'assistant' | 'tool'
  content    TEXT,
  tool_name  TEXT,               -- set for role='tool'
  created_at TEXT                -- ISO-8601 UTC
)
```

---

## Trade-offs considered

| Decision | Alternative | Why I chose this |
|---|---|---|
| Gemini native function calling | Prompt-engineered ReAct | More reliable, structured, no parsing |
| SQLite | Redis / Postgres | Zero-dependency, perfect for single-process |
| Tavily | SerpAPI / Bing | LLM-optimised output, generous free tier |
| Vanilla CSS | TailwindCSS | No build step, full control, smaller bundle |
| FastAPI | Flask | Native async, automatic OpenAPI docs, SSE support |
| No LangChain | LangChain | Simpler, faster, more debuggable at this scale |

---

## What I'd improve with more time

1. **Streaming tokens from Gemini** — currently Gemini doesn't stream function-call intermediate steps; with the streaming API I'd yield real tokens during generation
2. **Vector memory** — embed past messages and do semantic search for cross-session recall
3. **Tool registry** — make tools pluggable so adding calculator, code interpreter, etc. is a one-liner
4. **Rate limiting + auth** — basic API key auth for multi-user deployments
5. **Evaluation** — a test suite that measures answer accuracy against a golden set of questions

---

## Running locally

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt

# 2. Set API keys
cp ../.env.example ../.env
# Edit .env: add GEMINI_API_KEY and optionally TAVILY_API_KEY

# 3. Start the server
python main.py

# 4. Open the UI
# http://localhost:8000
```
