# EasyMultiTenantOpenClaw

Give each [OpenWebUI](https://github.com/open-webui/open-webui) user a
dedicated [OpenClaw](https://openclaw.ai) gateway container, so cron jobs,
credentials, and skill-runtime state don't leak between users.

## Problem

OpenClaw is a single-user agent runtime. Drop it behind a multi-user
OpenWebUI and the isolation breaks in several places:

| Shared resource | Evidence |
|---|---|
| Cron scheduler | `~/.openclaw/cron/jobs.json` is a single file; no `agentId` field on jobs |
| Credentials | `~/.openclaw/credentials/*.json` keyed by channel (discord, slack, …), not by user |
| Exec approvals | `exec-approvals.json` has an `agents: {}` field but its socket is a global singleton |
| Skill runtime | One `tavily` API key, one bash execution env, shared by every agent |

## Approach

Spin up one OpenClaw container per OpenWebUI user. A light FastAPI router
sits in front of the containers and dispatches each request by reading
the `X-OpenWebUI-User-Id` header that OpenWebUI attaches when
`ENABLE_FORWARD_USER_INFO_HEADERS=true`.

```
OpenWebUI (:9798)
  └─ openai connection → http://host.docker.internal:18888/v1  (router)
                                  │ X-OpenWebUI-User-Id
                                  │ lookup tenants.json
                                  ▼
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
               :18800 user-a  :18801 user-b  :18802 user-c
               openclaw-user-a  …           …
               volumes/user-a/  volumes/user-b/  volumes/user-c/
```

Everything in `container-orch/` is the per-user isolation stack; everything
in `bridge/` is a small admin UI that pre-dates the isolation work and
helps manage agents on a single shared gateway (kept here because it
shares the agent vocabulary and is used during migration from shared to
isolated mode).

## Components

- **[`container-orch/`](container-orch/README.md)** — Dockerfile, compose
  file, router, provisioning script. Start here if you want to stand up
  the isolation stack.
- **[`bridge/`](bridge/)** — shared-gateway agent-management UI. Useful
  as a reference for the original CRUD and when you keep legacy agents on
  a shared `~/.openclaw/` during migration.

## Status

Proof-of-concept, validated locally with 3 containers against OpenWebUI
v0.8.12 + OpenClaw 2026.4.7. Full isolation confirmed end-to-end: each
user only sees their own workspace model, cross-user model IDs are
rejected by OpenWebUI, and per-container filesystems are separate.

Production open questions:

- Always-on containers cost ~450 MB RSS each — 100 users ≈ 45 GB. Needs
  a real server, or a lazy-start/autoscale orchestrator for desktop.
- `tenants.json` is a flat JSON file — fine for hundreds of entries, not
  for tens of thousands.
- No migration path yet for existing shared-gateway agents; a future
  `migrate.py` can move `~/openclaw-workspaces/web-*` into per-tenant
  volumes.

## License

MIT
