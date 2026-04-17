"""
openclaw-bridge FastAPI app.

Endpoints:
  GET  /                     HTML UI (agent list + new/delete)
  GET  /api/agents           list bridge agents (JSON)
  POST /api/agents           create agent
  DELETE /api/agents/{id}    delete agent (bridge-managed only)

Run:  cd ~/Workspace/openwebuiEx/bridge && python main.py
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import agents

app = FastAPI(title="openclaw-bridge", version="0.1.0")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class CreateAgentBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    agent_list = agents.list_agents()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "agents": agent_list},
    )


@app.get("/api/agents")
def api_list():
    return agents.list_agents()


@app.post("/api/agents")
def api_create(body: CreateAgentBody):
    try:
        return agents.create_agent(body.name, body.description)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/agents/{agent_id}")
def api_delete(agent_id: str):
    try:
        agents.delete_agent(agent_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18790, log_level="info")
