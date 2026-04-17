#!/usr/bin/env python3
"""
Provision 3 demo tenants end-to-end:

1. Read gateway tokens from each container's openclaw.json
2. Create OpenWebUI user accounts (demo01/02/03)
3. Add "openclaw-isolated" connection in OpenWebUI (pointing to router)
4. Build tenants.json mapping user_id → container
5. Create OpenWebUI workspace models bound to each user

Prerequisites:
  - 3 containers running (docker compose up -d)
  - Router running on :18888
  - OpenWebUI running on :9798 with ENABLE_FORWARD_USER_INFO_HEADERS=true

Usage:
  python3 scripts/provision_demo_tenants.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

OPENWEBUI = os.getenv("OWUI_BASE_URL", "http://127.0.0.1:9798")
ADMIN_EMAIL = os.getenv("OWUI_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("OWUI_ADMIN_PASSWORD", "")
ROUTER_PORT = int(os.getenv("ROUTER_PORT", "18888"))
VOLUMES_DIR = Path(__file__).resolve().parent.parent / "volumes"
TENANTS_FILE = Path(__file__).resolve().parent.parent / "tenants.json"

DEMOS = [
    {"name": "demo01", "port": 18800, "container": "openclaw-demo01",
     "user_name": "隔离员工A", "email": "iso-demo01@demo.local", "password": "Demo!Pass01"},
    {"name": "demo02", "port": 18801, "container": "openclaw-demo02",
     "user_name": "隔离员工B", "email": "iso-demo02@demo.local", "password": "Demo!Pass02"},
    {"name": "demo03", "port": 18802, "container": "openclaw-demo03",
     "user_name": "隔离员工C", "email": "iso-demo03@demo.local", "password": "Demo!Pass03"},
]


def req(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} {url}: {text}")


def admin_login() -> str:
    resp = req("POST", f"{OPENWEBUI}/api/v1/auths/signin",
               {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return resp["token"]


def get_gateway_token(name: str) -> str:
    cfg_path = VOLUMES_DIR / name / "openclaw.json"
    with open(cfg_path) as f:
        cfg = json.load(f)
    return cfg["gateway"]["auth"]["token"]


def ensure_connection(admin_token: str) -> int:
    """Add 'openclaw-isolated' connection if not present.
    Returns the urlIdx for the new connection."""
    resp = req("GET", f"{OPENWEBUI}/openai/config", token=admin_token)
    base_urls = resp.get("OPENAI_API_BASE_URLS", [])
    keys = resp.get("OPENAI_API_KEYS", [])
    configs = resp.get("OPENAI_API_CONFIGS", {})

    router_url = f"http://host.docker.internal:{ROUTER_PORT}/v1"

    # Check if already exists
    for i, url in enumerate(base_urls):
        if str(ROUTER_PORT) in url:
            print(f"  connection already exists at index {i}")
            return i

    # Add new connection
    base_urls.append(router_url)
    keys.append("router-placeholder-key")
    idx = len(base_urls) - 1
    configs[str(idx)] = {"prefix_id": "openclaw-isolated"}

    req("POST", f"{OPENWEBUI}/openai/config/update", {
        "ENABLE_OPENAI_API": True,
        "OPENAI_API_BASE_URLS": base_urls,
        "OPENAI_API_KEYS": keys,
        "OPENAI_API_CONFIGS": configs,
    }, token=admin_token)
    print(f"  added connection at index {idx}: {router_url}")
    return idx


def main():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        sys.exit(
            "error: set OWUI_ADMIN_EMAIL and OWUI_ADMIN_PASSWORD env vars\n"
            "  export OWUI_ADMIN_EMAIL='admin@example.com'\n"
            "  export OWUI_ADMIN_PASSWORD='your-password'"
        )

    print("=== Admin login ===")
    admin_token = admin_login()
    print(f"  OK (token {admin_token[:20]}...)")

    print("\n=== Read gateway tokens ===")
    for d in DEMOS:
        d["gateway_token"] = get_gateway_token(d["name"])
        print(f"  {d['name']}: {d['gateway_token'][:16]}...")

    print("\n=== Ensure OpenWebUI connection ===")
    url_idx = ensure_connection(admin_token)

    print("\n=== Create OpenWebUI users ===")
    for d in DEMOS:
        try:
            resp = req("POST", f"{OPENWEBUI}/api/v1/auths/add", {
                "name": d["user_name"],
                "email": d["email"],
                "password": d["password"],
                "role": "user",
            }, token=admin_token)
            d["user_id"] = resp["id"]
            print(f"  {d['email']} → {d['user_id']}")
        except RuntimeError as e:
            if "EXISTING_USERS" in str(e) or "already" in str(e).lower():
                # User exists — look up their id
                resp = req("POST", f"{OPENWEBUI}/api/v1/auths/signin",
                           {"email": d["email"], "password": d["password"]})
                d["user_id"] = resp["id"]
                print(f"  {d['email']} → {d['user_id']} (existing)")
            else:
                raise

    print("\n=== Write tenants.json ===")
    tenants_data = {
        "version": 1,
        "tenants": {
            d["user_id"]: {
                "port": d["port"],
                "profile": d["name"],
                "container": d["container"],
                "gateway_token": d["gateway_token"],
                "openwebui_model_id": f"{d['name']}-agent",
            }
            for d in DEMOS
        },
    }
    tmp = TENANTS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(tenants_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, TENANTS_FILE)
    print(f"  written {TENANTS_FILE}")

    print("\n=== Create workspace models ===")
    for d in DEMOS:
        model_id = f"{d['name']}-agent"
        try:
            resp = req("POST", f"{OPENWEBUI}/api/v1/models/create", {
                "id": model_id,
                "base_model_id": "openclaw-isolated/openclaw",
                "name": f"{d['user_name']}的隔离助手",
                "meta": {"description": f"仅 {d['user_name']} 可见 (容器隔离)"},
                "params": {},
                "access_grants": [{
                    "principal_type": "user",
                    "principal_id": d["user_id"],
                    "permission": "read",
                }],
                "is_active": True,
            }, token=admin_token)
            print(f"  {model_id} → bound to {d['user_name']}")
        except RuntimeError as e:
            if "TAKEN" in str(e) or "already" in str(e).lower():
                print(f"  {model_id} already exists (skip)")
            else:
                raise

    print("\n=== Summary ===")
    for d in DEMOS:
        print(f"  {d['email']} / {d['password']}  →  model={d['name']}-agent  →  container={d['container']}:{d['port']}")

    print(f"\nDONE. Login to {OPENWEBUI} with any demo account.")


if __name__ == "__main__":
    main()
