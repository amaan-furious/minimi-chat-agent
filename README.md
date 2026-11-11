# minimi Chat Agent

> A ReAct agent with real-time web search, streaming responses, and timestamp-aware persistent memory.

## Demo

Start the server and open `http://localhost:8000` in your browser.

## Setup

### 1. Get API keys

| Key | Where | Required? |
|---|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) | **Yes** |
| `TAVILY_API_KEY` | [Tavily](https://app.tavily.com) | Optional (falls back to DuckDuckGo) |

### 2. Install & run

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in your keys
python main.py
```

Open → **http://localhost:8000**

## Architecture

See [`design.md`](design.md) for the full architecture document.

**Quick summary:**
- **ReAct loop** via Gemini 1.5 Flash native function calling
- **Streaming** via Server-Sent Events (SSE)
- **Persistent memory** in SQLite with ISO timestamps
- **Web search** via Tavily (Gemini-optimised) or DuckDuckGo fallback
- **No LangChain** — explicit, readable agent loop in ~80 lines

## Project structure

```
backend/
  main.py        FastAPI app + SSE endpoint
  agent.py       ReAct agent loop
  memory.py      SQLite memory store
  search.py      Tavily + DuckDuckGo
  prompts.py     System prompts
frontend/
  index.html
  style.css
  app.js
design.md        Architecture document
```
