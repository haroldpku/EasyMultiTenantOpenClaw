# Contributing to EasyMultiTenantOpenClaw

Thanks for your interest in the project. This guide orients new contributors: what the codebase is, how the pieces fit together, how to get a working dev environment, how to extend it, and how we expect patches to land.

## Project purpose

EasyMultiTenantOpenClaw turns [OpenClaw](https://openclaw.ai) into a multi-tenant backend for [OpenWebUI](https://openwebui.com) **without modifying either upstream codebase**. OpenClaw by itself is a single-user agent gateway: cron jobs, credentials, execution-approval dialogs, and the bash skill runtime are all shared across whoever uses that OpenClaw instance. That is fine for one person on a laptop, and unsafe for any deployment where users should not see each other's API keys, schedules, or files.

This repo solves that problem externally. OpenClaw sees OpenWebUI as just another inbound **channel** (conceptually the same as its Telegram or Slack integrations). OpenWebUI sees OpenClaw as just another OpenAI-compatible **model** reachable at `/v1/chat/completions`. Between them sits a thin FastAPI router and a container-per-tenant layout that maps each OpenWebUI user to a dedicated OpenClaw container with its own volume. OpenWebUI already has users, workspace-models, and `access_grants` primitives — we push those primitives "down" into OpenClaw by giving each user their own isolated process and filesystem. The net effect is enterprise-style multi-tenancy while upstream OpenClaw source stays untouched.

## Architecture summary

The repo has two top-level directories and they target two different deployment modes. **Know which one you are changing before opening a PR.**

- **`container-orch/`** is the production path and the one `install.sh` wires up. It builds a reusable `openclaw:base` image (`Dockerfile` + `start-openclaw.sh`, with `link-extension-deps.sh` patching bundled channel plugins such as `@buape/carbon`), runs N tenant containers and a `router/` FastAPI service via `docker-compose.yml`, and uses `scripts/provision_demo_tenants.py` to create OpenWebUI users, an `openclaw-isolated` connection, the `tenants.json` mapping, and per-user workspace models with `access_grants`. Traffic flows `OpenWebUI → router:18888 → openclaw-demo0X:18789`, dispatched on the `X-OpenWebUI-User-Id` header.
- **`bridge/`** is an older, single-OpenClaw mode kept as a reference. It is a small FastAPI app (`main.py` + `agents.py` + `config.py` + a Jinja `templates/index.html`) that manages `web-*` agents by editing `~/.openclaw/openclaw.json` in place and creating per-agent workspaces under `~/openclaw-workspaces/`. It is useful during migration off the shared-gateway model and is not started by `install.sh`.

## Dev setup

The canonical path is the one-shot installer at the root of the repo:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/haroldpku/EasyMultiTenantOpenClaw/main/install.sh)
```

Running that directly from a local clone (`./install.sh`) also works — `install.sh` is idempotent. Read the script before running it: step 1 preflights docker/compose/git/curl/python3, step 2 clones or `git pull`s `$INSTALL_DIR` (default `~/EasyMultiTenantOpenClaw`), step 3 prompts for `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `DASHSCOPE_KEY` (all pre-settable via env for CI). Step 4 starts an `open-webui` container on `:9798` with `ENABLE_FORWARD_USER_INFO_HEADERS=true`. Step 5 calls `/api/v1/auths/signup` for the admin, falling back to `/signin` if the account already exists. Step 6 builds `openclaw:base`. Step 7 runs `docker compose up -d --build` and waits on each tenant's `/v1/models`. Step 8 injects a Dashscope provider block into each tenant's `volumes/demoXX/openclaw.json`, restarts the tenants, and runs `provision_demo_tenants.py`. Step 9 prints a summary with three demo logins (`iso-demo0N@demo.local` / `Demo!Pass0N`).

To iterate on the `bridge/` app standalone, create a venv, `pip install -r bridge/requirements.txt`, and run `python bridge/main.py` — it serves on `127.0.0.1:18790` and edits `~/.openclaw/openclaw.json` directly, so make sure you have an OpenClaw config to point at.

## How to add a new tenant

The POC ships three demo tenants because `container-orch/docker-compose.yml` declares exactly three `openclaw-demoXX` services and `install.sh` hard-codes `TENANT_COUNT=3`. Adding a fourth (or Nth) tenant is a three-file change plus a volume bootstrap:

1. **Declare the container.** In `container-orch/docker-compose.yml`, copy one of the `openclaw-demo0N` blocks to a new name (e.g. `openclaw-demo04`), bump the host port so it stays unique (the existing convention is `18800 + (N-1)`, keeping the in-container port pinned at `18789`), and point the volume bind at `./volumes/demo04:/data`.
2. **Bump the installer counter.** Change `TENANT_COUNT` in `install.sh` so steps 7 and 8 provision the new tenant. The port math (`port=$((18799 + i))`) and volume path (`volumes/demo$(printf '%02d' "$i")`) both flow from that single variable.
3. **Create the volume and seed config.** `mkdir -p container-orch/volumes/demo04` before `docker compose up -d` — `start-openclaw.sh` writes the first-boot `openclaw.json` inside the container on empty volumes, and step 8 injects the Dashscope provider afterwards.
4. **Extend provisioning.** `container-orch/scripts/provision_demo_tenants.py` is what binds OpenWebUI users to their container; review that script for any hardcoded demo list and add the new user there, or generalize the loop over `TENANT_COUNT`.

For a one-off tenant outside the demo convention, you can also land entries directly in `tenants.json` (the router reads it at runtime) and skip the compose/installer edits, but that path is not covered by `install.sh` today.

## How to run tests

There is **no automated test suite in the repo today** — that is deliberate for a POC but is the single biggest contribution opportunity. When you add tests, use this layout:

- `bridge/` → `pytest` with fixtures that point `OPENCLAW_JSON` and `WORKSPACE_ROOT` at a `tmp_path`, then round-trip `config.load/save`, `ensure_main_placeholder`, and the `agents.create_agent`/`delete_agent` flow (including the "refuses to delete non-`web-` ids" `PermissionError` and the `.trash/` move). These are pure-Python, no docker needed, and should run on every push.
- `container-orch/router/` → `pytest` with `fastapi.testclient.TestClient`, using a fake `tenants.json` to assert that the correct upstream is picked for a given `X-OpenWebUI-User-Id` and that missing headers return the right HTTP status.
- Tenant image smoke → a shell test that runs `docker build -t openclaw:base container-orch`, starts one container with a scratch volume, polls `/v1/models`, and exits non-zero if the gateway is not reachable within a bounded timeout. Ideally wired into `install.sh` as an optional `--smoke` flag or as a GitHub Actions job.
- End-to-end isolation → a test that creates two tenants, has each write a distinct file into its volume via `docker exec`, and asserts that neither can read the other's `/volumes/*` directory and that an `access_grants` mismatch in OpenWebUI returns `Model not found`. This mirrors the "Verified isolation" table in `README.md` and is what a maintainer would run before tagging a release.

Until those exist, PRs must document their manual verification steps (commands, expected output, container logs) in the PR description.

## Commit and PR conventions

- **Branches.** Feature branches use `feat/<slug>`, fixes use `fix/<slug>`, docs use `docs/<slug>`. Target `main`.
- **Commit messages.** Follow Conventional Commits. Type prefixes: `feat:` (new capability), `fix:` (bug), `docs:` (docs-only), `refactor:` (no behavior change), `chore:` (tooling / deps), `test:` (tests only). Subject line is imperative and under 72 chars; wrap the body at ~80 chars and explain *why* the change is needed, not just what it does.
- **Scope per PR.** One logical change per PR. Do not bundle an unrelated refactor with a fix. Touching `bridge/` and `container-orch/` in the same PR is fine only when a contract genuinely spans both; otherwise split.
- **Two sources of truth.** `README.md` and `README.zh.md` must stay in sync. Any user-visible change in one needs a matching update in the other within the same PR.
- **Do not modify upstream.** The project's whole thesis is that OpenClaw and OpenWebUI sources stay untouched. If a feature seems to require patching either, open an issue first — there is almost always a way to express it as router logic, a tenant volume config change, or a provisioning-script update.
- **Reviews.** At least one maintainer approval before merge. Squash-merge by default so each PR becomes a single, reviewable commit on `main`. Mentioning `@claude` in an issue or PR body invokes the [Claude Code action](.github/workflows/claude.yml) for a machine-assisted read-through or a follow-up PR; see [.github/CLAUDE-ACTION-SETUP.md](.github/CLAUDE-ACTION-SETUP.md) for required secrets.
- **Security.** Never commit real API keys. `DASHSCOPE_KEY`, OpenWebUI admin creds, and any tenant-specific secrets always flow through environment variables or `volumes/*/openclaw.json` (which is gitignored).
