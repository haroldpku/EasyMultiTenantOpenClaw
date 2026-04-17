# container-orch

Per-user OpenClaw container isolation POC. Each OpenWebUI user gets their own
dedicated OpenClaw gateway container, so cron jobs, credentials, exec-approvals,
and skill runtime don't leak between users.

## Why

OpenClaw is a single-user runtime. When it sits behind a multi-user OpenWebUI,
shared state becomes a problem:

- `~/.openclaw/cron/jobs.json` is a single file — user A's scheduled tasks
  show up for user B
- `~/.openclaw/credentials/*.json` is channel-keyed, not user-keyed
- `exec-approvals.json` has one global socket
- All 52 bundled skills share one `tavily` API key, one bash execution env

Per-user containers give hard isolation for all four without changing
OpenClaw upstream.

## Architecture

```
OpenWebUI (docker: open-webui, :9798)
  └─ connection "openclaw-isolated" → http://host.docker.internal:18888/v1
                                       │
                                       │ read X-OpenWebUI-User-Id header
                                       │ (OpenWebUI auto-sets this when
                                       │  ENABLE_FORWARD_USER_INFO_HEADERS=true)
                                       │ lookup in tenants.json
                                       ▼
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                   :18800 demo01  :18801 demo02  :18802 demo03
                   (3 docker containers, one openclaw:base image,
                    separate volumes: volumes/demo01, volumes/demo02, ...)
```

## Components

- `Dockerfile` — `openclaw:base` image. Wraps `openclaw@2026.4.7` with a
  first-boot bootstrap script and a symlink fixup for bundled channel plugins.
- `start-openclaw.sh` — container entrypoint. Seeds `/data/openclaw.json` with
  a random gateway token on first boot, then execs `openclaw gateway`.
- `link-extension-deps.sh` — build-time: symlinks every extension's
  `node_modules/*` into the openclaw package root so Node's resolver can
  find them (bundled extensions ship deps at the wrong depth otherwise).
- `docker-compose.yml` — declares 3 demo containers, ports 18800–18802,
  volumes `volumes/demo01..03/`.
- `router/main.py` — FastAPI proxy on `:18888`. Reads `X-OpenWebUI-User-Id`,
  looks up the user's container in `tenants.json`, forwards with the
  tenant's token. `GET /v1/models` also works without the header (needed
  for OpenWebUI's connection-level probe).
- `tenants.json` — runtime-generated mapping; see `tenants.example.json`.
- `scripts/provision_demo_tenants.py` — end-to-end provisioning: creates
  OpenWebUI users + adds the `openclaw-isolated` connection + builds
  `tenants.json` + creates workspace models bound per-user.

## Quickstart

Prereqs: docker, OpenWebUI running on :9798 with
`ENABLE_FORWARD_USER_INFO_HEADERS=true`, admin account for OpenWebUI.

```bash
docker build -t openclaw:base .
docker compose up -d                       # 3 containers, wait ~5s

cd router && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py &                 # router on :18888

# Edit admin creds in scripts/provision_demo_tenants.py, then:
python3 scripts/provision_demo_tenants.py
```

You'll get 3 OpenWebUI accounts (`iso-demo0{1,2,3}@demo.local` / `Demo!Pass0X`)
each seeing one isolated model.

## Verify isolation

After one user creates a cron job or saves a credential:

```bash
cat volumes/demo01/cron/jobs.json     # has the job
cat volumes/demo02/cron/jobs.json     # empty / not affected
docker exec openclaw-demo01 ls /data  # sees own state
docker exec openclaw-demo01 ls /volumes/demo02   # does not exist
```

## Teardown

```bash
./scripts/teardown.sh            # stop containers, keep volumes
./scripts/teardown.sh --purge    # stop + delete volumes + tenants.json
```

OpenWebUI users and the `openclaw-isolated` connection are left intact by
both variants — remove them manually from the admin panel if needed.

## Known limits (POC scope)

- Home-local only. Each container is ~450 MB RSS, so 100 users ≈ 45 GB
  — needs a real server, not this 16 GB Mac.
- Each container pins `dashscope/qwen3-max` via `volumes/demo0X/openclaw.json`.
  Swap the provider there for production.
- No orchestrator for lazy start/stop or autoscale; containers are always-on.
- `tenants.json` is a flat file, not a DB; fine for 100 entries, not for 10k.
