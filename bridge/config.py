"""
openclaw.json read/write helper.

- Atomic write via tmp + os.replace.
- On first-ever write, preserves a .bridge-bak of the original config.
- Only touches agents.list; other fields (credentials, channels, gateway, ...)
  pass through unchanged because we round-trip the whole dict.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

OPENCLAW_JSON = Path.home() / ".openclaw" / "openclaw.json"
BRIDGE_BAK = Path.home() / ".openclaw" / "openclaw.json.bridge-bak"

MAIN_AGENT_ID = "main"


def load() -> dict[str, Any]:
    with open(OPENCLAW_JSON) as f:
        return json.load(f)


def save(cfg: dict[str, Any]) -> None:
    # First-ever backup: capture pristine state before any bridge write.
    if not BRIDGE_BAK.exists():
        shutil.copy2(OPENCLAW_JSON, BRIDGE_BAK)

    tmp = OPENCLAW_JSON.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, OPENCLAW_JSON)


def ensure_main_placeholder(cfg: dict[str, Any]) -> None:
    """
    Protect the user's fallback 'main' agent from disappearing when we add
    the first entry to agents.list.

    OpenClaw's listAgentIds (src/agents/agent-scope.ts:63-78) returns
    [DEFAULT_AGENT_ID] only when agents.list is empty. The moment we append
    any entry, the fallback stops firing and the user loses openclaw/main.

    Fix: if there's no main entry in the list, insert a placeholder with
    just id+name+default. We deliberately omit 'workspace' so that
    resolveAgentWorkspaceDir falls back to agents.defaults.workspace,
    preserving wherever the user's real main workspace is.
    """
    agents = cfg.setdefault("agents", {})
    agents_list = agents.setdefault("list", [])

    if any(a.get("id") == MAIN_AGENT_ID for a in agents_list):
        return

    agents_list.insert(0, {
        "id": MAIN_AGENT_ID,
        "name": "Main",
        "default": True,
    })
