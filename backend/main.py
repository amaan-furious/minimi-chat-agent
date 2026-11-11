"""
main.py — FastAPI application.

Endpoints:
  POST /sessions            — create a new chat session
  GET  /sessions            — list all sessions
  GET  /sessions/{id}       — get session details + messages
  POST /chat                — send a message, receive SSE stream
  GET  /health              — health check
"""

import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

load_dotenv()

from memory import (
    init_db,
    create_session,
    list_sessions,
    get_session,
    get_messages,
)
from agent import run_agent


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="minimi Chat Agent",
    description="ReAct agent with web search and persistent memory",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str


class NewSessionRequest(BaseModel):
    title: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/sessions")
async def new_session(req: NewSessionRequest = None):
    if req is None:
        req = NewSessionRequest()
    return create_session(req.title)


@app.get("/sessions")
async def sessions():
    return list_sessions()


@app.get("/sessions/{session_id}")
async def session_detail(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_messages(session_id)
    return {"session": session, "messages": messages}


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Stream a response as Server-Sent Events.

    Each event is a JSON string with shape:
      {"type": "token" | "tool_call" | "tool_result" | "done" | "error", "data": ...}
    """
    session = get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        async for event_json in run_agent(req.session_id, req.message):
            yield {"data": event_json}

    return EventSourceResponse(event_generator())


# ── Static files (serve frontend) ────────────────────────────────────────────

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # reload=True requires watchfiles which needs Rust
    )
