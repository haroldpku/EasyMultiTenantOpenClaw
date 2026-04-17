"""
Read-only tenants.json loader.

Schema:
{
  "version": 1,
  "tenants": {
    "<openwebui-user-id>": {
      "port": 18800,
      "profile": "demo01",
      "container": "openclaw-demo01",
      "gateway_token": "...",
      "openwebui_model_id": "demo01-agent"
    }
  }
}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

TENANTS_FILE = Path(__file__).resolve().parent.parent / "tenants.json"

_cache: dict | None = None
_mtime: float = 0


def _reload_if_changed() -> dict:
    global _cache, _mtime
    try:
        st = TENANTS_FILE.stat()
    except FileNotFoundError:
        _cache = {"version": 1, "tenants": {}}
        return _cache
    if _cache is None or st.st_mtime != _mtime:
        with open(TENANTS_FILE) as f:
            _cache = json.load(f)
        _mtime = st.st_mtime
    return _cache


def lookup(user_id: str) -> Optional[dict]:
    """Return tenant entry for a given OpenWebUI user_id, or None."""
    data = _reload_if_changed()
    return data.get("tenants", {}).get(user_id)


def all_tenants() -> dict[str, dict]:
    """Return all tenant entries keyed by user_id."""
    data = _reload_if_changed()
    return data.get("tenants", {})
