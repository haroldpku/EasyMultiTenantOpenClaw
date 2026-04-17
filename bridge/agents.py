"""
Bridge agent CRUD.

- Agent IDs are 'web-' + 8 hex chars (collision-safe for a single user).
- Bridge only operates on 'web-' prefixed entries; user's existing
  main/ops/etc. agents are never touched.
- Rich metadata (description, created_at) lives in a bridge-owned
  registry file, NOT in openclaw.json — that way openclaw.json stays
  free of bridge-specific fields and passes OpenClaw's schema cleanly.
"""
from __future__ import annotations

import getpass
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

import config

WORKSPACE_ROOT = Path.home() / "openclaw-workspaces"
TRASH_ROOT = WORKSPACE_ROOT / ".trash"
REGISTRY = WORKSPACE_ROOT / ".bridge-registry.json"
BRIDGE_PREFIX = "web-"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_agent_id() -> str:
    return BRIDGE_PREFIX + secrets.token_hex(4)


def _workspace_abs(agent_id: str) -> Path:
    return WORKSPACE_ROOT / agent_id


def _load_registry() -> dict:
    if not REGISTRY.exists():
        return {"agents": {}}
    with open(REGISTRY) as f:
        return json.load(f)


def _save_registry(reg: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, REGISTRY)


def list_agents() -> list[dict]:
    """List bridge-managed agents by reading openclaw.json + registry."""
    cfg = config.load()
    agents_list = cfg.get("agents", {}).get("list", [])
    reg = _load_registry()
    reg_map = reg.get("agents", {})

    out = []
    for entry in agents_list:
        aid = entry.get("id", "")
        if not aid.startswith(BRIDGE_PREFIX):
            continue
        meta = reg_map.get(aid, {})
        out.append({
            "id": aid,
            "name": entry.get("name", ""),
            "description": meta.get("description", ""),
            "workspace": entry.get("workspace", ""),
            "created_at": meta.get("created_at", ""),
        })
    return out


def create_agent(name: str, description: str) -> dict:
    """
    Create a new agent with isolated workspace + openclaw.json entry.

    Order:
      1. Create workspace on disk (SOUL.md / USER.md / memory/)
      2. Update registry
      3. Update openclaw.json (with main placeholder protection)

    Rollback: if any step after (1) fails, the workspace is removed.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")

    agent_id = _new_agent_id()
    ws = _workspace_abs(agent_id)
    now = _iso_now()

    # 1. Create workspace directory structure
    ws.mkdir(parents=True, exist_ok=False)
    (ws / "memory").mkdir()

    try:
        (ws / "SOUL.md").write_text(
            f"# SOUL\n\n{description or name}\n\n"
            f"<!-- Created by openclaw-bridge at {now} -->\n",
            encoding="utf-8",
        )
        (ws / "USER.md").write_text(
            "# USER\n\n"
            f"Name: {getpass.getuser()}\n"
            f"Created: {now}\n"
            "Managed by: openclaw-bridge\n",
            encoding="utf-8",
        )

        # 2. Registry (bridge-owned metadata)
        reg = _load_registry()
        reg.setdefault("agents", {})[agent_id] = {
            "name": name,
            "description": description or "",
            "created_at": now,
        }
        _save_registry(reg)

        # 3. openclaw.json (OpenClaw-visible config)
        cfg = config.load()
        config.ensure_main_placeholder(cfg)
        cfg["agents"]["list"].append({
            "id": agent_id,
            "name": name,
            "workspace": f"~/openclaw-workspaces/{agent_id}",
            # Pin agentDir under the workspace so all per-agent state
            # (sessions, workspace-state.json, etc.) lives in one root.
            # Without this, OpenClaw falls back to ~/.openclaw/agents/<id>/
            # which strands sessions outside the workspace on delete.
            "agentDir": f"~/openclaw-workspaces/{agent_id}/.agent-state",
        })
        config.save(cfg)

    except Exception:
        if ws.exists():
            shutil.rmtree(ws)
        # Best-effort registry rollback
        try:
            reg = _load_registry()
            if agent_id in reg.get("agents", {}):
                del reg["agents"][agent_id]
                _save_registry(reg)
        except Exception:
            pass
        raise

    return {
        "agent_id": agent_id,
        "openai_model": f"openclaw/{agent_id}",
        "workspace": str(ws),
    }


def delete_agent(agent_id: str) -> None:
    """
    Remove a bridge agent:
      1. Validate it's a bridge-managed id (refuses others)
      2. Remove from openclaw.json
      3. Remove from registry
      4. Move workspace to .trash/
    """
    if not agent_id.startswith(BRIDGE_PREFIX):
        raise PermissionError(
            f"refuses to delete agents not managed by bridge (id must start with '{BRIDGE_PREFIX}')"
        )

    cfg = config.load()
    agents_list = cfg.get("agents", {}).get("list", [])
    new_list = [a for a in agents_list if a.get("id") != agent_id]
    if len(new_list) == len(agents_list):
        raise LookupError(f"agent {agent_id} not found in openclaw.json")

    cfg.setdefault("agents", {})["list"] = new_list
    config.save(cfg)

    reg = _load_registry()
    reg.get("agents", {}).pop(agent_id, None)
    _save_registry(reg)

    ts = _iso_now().replace(":", "-")
    TRASH_ROOT.mkdir(parents=True, exist_ok=True)

    ws = _workspace_abs(agent_id)
    if ws.exists():
        shutil.move(str(ws), str(TRASH_ROOT / f"{agent_id}_{ts}"))

    # Fallback cleanup: OpenClaw's default agentDir location.
    # New agents have an explicit agentDir under workspace (trashed above),
    # but legacy agents or agents created without that field stranded their
    # sessions/ here. Trash those too so delete leaves nothing behind.
    default_agent_dir = Path.home() / ".openclaw" / "agents" / agent_id
    if default_agent_dir.exists():
        shutil.move(
            str(default_agent_dir),
            str(TRASH_ROOT / f"{agent_id}_agentdir_{ts}"),
        )
