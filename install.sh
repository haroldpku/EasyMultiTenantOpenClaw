#!/usr/bin/env bash
# EasyMultiTenantOpenClaw — full-stack one-shot installer.
#
# One-liner (from a clean Ubuntu box):
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/haroldpku/EasyMultiTenantOpenClaw/main/install.sh)
#
# Non-interactive (for CI / scripted installs):
#
#   ADMIN_EMAIL=admin@example.com \
#   ADMIN_PASSWORD=changeme \
#   DASHSCOPE_KEY=sk-xxxxxxxx \
#   INSTALL_DIR=~/emt-openclaw \
#   bash <(curl -fsSL https://raw.githubusercontent.com/haroldpku/EasyMultiTenantOpenClaw/main/install.sh)
#
# What it does (idempotent where possible):
#   1. Preflight: docker, docker compose, git, curl, python3 installed?
#   2. Clone (or update) the repo to $INSTALL_DIR
#   3. Start OpenWebUI in a docker container with the right env
#   4. Register the admin account via /api/v1/auths/signup
#   5. Build openclaw:base (tenant image, ~1.7 GB)
#   6. docker compose up -d (router + 3 tenants)
#   7. Inject Dashscope provider config into each tenant volume
#   8. Run provision_demo_tenants.py — creates 3 demo users +
#      openclaw-isolated connection + per-user workspace models
#   9. Print credentials summary
#
# Re-running is safe: existing containers/users are detected and reused.

set -euo pipefail

# ---------- knobs / defaults ----------
INSTALL_DIR="${INSTALL_DIR:-$HOME/EasyMultiTenantOpenClaw}"
REPO_URL="${REPO_URL:-https://github.com/haroldpku/EasyMultiTenantOpenClaw.git}"
OPENWEBUI_PORT="${OPENWEBUI_PORT:-9798}"
OPENWEBUI_IMAGE="${OPENWEBUI_IMAGE:-ghcr.io/open-webui/open-webui:main}"
TENANT_COUNT=3  # compose has 3 tenants today; increase by editing docker-compose.yml

# Credentials (prompted if unset):
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
ADMIN_NAME="${ADMIN_NAME:-Admin}"
DASHSCOPE_KEY="${DASHSCOPE_KEY:-}"
DASHSCOPE_MODEL="${DASHSCOPE_MODEL:-qwen3-max}"
DASHSCOPE_BASE_URL="${DASHSCOPE_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"

# ---------- ui helpers ----------
bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "\033[36m◆\033[0m %s\n" "$*"; }
ok()   { printf "\033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

# ---------- 1. preflight ----------
step1_preflight() {
    bold "[1/9] Preflight check"
    local miss=()
    command -v docker >/dev/null || miss+=("docker")
    docker compose version >/dev/null 2>&1 || miss+=("docker-compose-plugin")
    command -v git >/dev/null || miss+=("git")
    command -v curl >/dev/null || miss+=("curl")
    command -v python3 >/dev/null || miss+=("python3")
    if [ ${#miss[@]} -gt 0 ]; then
        warn "missing: ${miss[*]}"
        cat <<EOF

Install on Ubuntu 22.04 / 24.04:

  # docker + compose plugin
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker \$USER && newgrp docker

  # the rest
  sudo apt-get update && sudo apt-get install -y git curl python3

Re-run this installer after that.
EOF
        die "preflight failed"
    fi
    if ! docker info >/dev/null 2>&1; then
        die "docker daemon not reachable. Is dockerd running? Are you in the 'docker' group?"
    fi
    ok "docker + git + curl + python3 ready"
}

# ---------- 2. clone / update ----------
step2_clone() {
    bold "[2/9] Clone / update repo at $INSTALL_DIR"
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "repo exists — git pull"
        (cd "$INSTALL_DIR" && git pull --ff-only) || warn "git pull failed, continuing with local copy"
    else
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
    ok "repo at $INSTALL_DIR"
}

# ---------- 3. credentials ----------
step3_prompt() {
    bold "[3/9] Credentials"
    if [ -z "$ADMIN_EMAIL" ]; then
        read -r -p "OpenWebUI admin email: " ADMIN_EMAIL
    fi
    if [ -z "$ADMIN_PASSWORD" ]; then
        read -r -s -p "OpenWebUI admin password: " ADMIN_PASSWORD; echo
    fi
    if [ -z "$DASHSCOPE_KEY" ]; then
        read -r -s -p "Dashscope API key (sk-...): " DASHSCOPE_KEY; echo
    fi
    [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ] && [ -n "$DASHSCOPE_KEY" ] \
        || die "all three credentials are required"
    ok "credentials collected"
}

# ---------- 4. openwebui ----------
step4_openwebui() {
    bold "[4/9] Start OpenWebUI on :$OPENWEBUI_PORT"
    if docker ps -a --format '{{.Names}}' | grep -qx open-webui; then
        # Ensure ENABLE_FORWARD_USER_INFO_HEADERS is present; if not, rebuild.
        if docker inspect open-webui --format '{{range .Config.Env}}{{println .}}{{end}}' \
             | grep -qx 'ENABLE_FORWARD_USER_INFO_HEADERS=true'; then
            info "open-webui container already has the right env; reusing"
            docker start open-webui >/dev/null 2>&1 || true
        else
            warn "open-webui exists without ENABLE_FORWARD_USER_INFO_HEADERS — recreating"
            docker rm -f open-webui >/dev/null
            _openwebui_run_fresh
        fi
    else
        _openwebui_run_fresh
    fi
    info "waiting for OpenWebUI to accept requests..."
    local tries=0
    until curl -fsS "http://127.0.0.1:$OPENWEBUI_PORT/api/config" >/dev/null 2>&1; do
        sleep 2
        tries=$((tries + 1))
        [ $tries -ge 60 ] && die "OpenWebUI didn't come up in 120s (check: docker logs open-webui)"
    done
    ok "OpenWebUI up at http://127.0.0.1:$OPENWEBUI_PORT"
}

_openwebui_run_fresh() {
    # --add-host host.docker.internal:host-gateway is required on Linux
    # so OpenWebUI can reach the router which is exposed on the host.
    # On Docker Desktop (Mac/Windows) the flag is a no-op.
    docker run -d \
        --name open-webui \
        -p "$OPENWEBUI_PORT:8080" \
        -v open-webui:/app/backend/data \
        --add-host host.docker.internal:host-gateway \
        -e WEBUI_AUTH=true \
        -e ENABLE_OPENAI_API=true \
        -e ENABLE_FORWARD_USER_INFO_HEADERS=true \
        --restart unless-stopped \
        "$OPENWEBUI_IMAGE" >/dev/null
}

# ---------- 5. admin account ----------
step5_admin() {
    bold "[5/9] Register admin account"
    local signup_resp
    signup_resp=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$ADMIN_NAME\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" \
        "http://127.0.0.1:$OPENWEBUI_PORT/api/v1/auths/signup")

    if echo "$signup_resp" | grep -q '"role":"admin"'; then
        ok "admin registered (first user on a fresh OpenWebUI)"
        return
    fi
    if echo "$signup_resp" | grep -q '"role":"pending"'; then
        die "signup created a PENDING user — OpenWebUI already has other users. \
Either (a) supply ADMIN_EMAIL/ADMIN_PASSWORD for an existing admin, or (b) \
reset by: docker rm -f open-webui && docker volume rm open-webui && re-run."
    fi
    # Signup failed (email exists, or signup disabled) — fall back to signin.
    local signin_resp
    signin_resp=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" \
        "http://127.0.0.1:$OPENWEBUI_PORT/api/v1/auths/signin")
    if echo "$signin_resp" | grep -q '"role":"admin"'; then
        ok "admin already exists — credentials verified"
    elif echo "$signin_resp" | grep -q '"token"'; then
        die "user '$ADMIN_EMAIL' exists but is not admin. Promote them in OpenWebUI or use an admin account."
    else
        die "signup + signin both failed:
signup: $signup_resp
signin: $signin_resp"
    fi
}

# ---------- 6. build openclaw:base ----------
step6_build() {
    bold "[6/9] Build openclaw:base image"
    if docker image inspect openclaw:base >/dev/null 2>&1; then
        info "openclaw:base already built; skipping (delete the image to force rebuild)"
        return
    fi
    (cd "$INSTALL_DIR/container-orch" && docker build -t openclaw:base .)
    ok "openclaw:base built"
}

# ---------- 7. compose up ----------
step7_compose_up() {
    bold "[7/9] docker compose up (router + $TENANT_COUNT tenants)"
    cd "$INSTALL_DIR/container-orch"
    # tenants.json must exist before compose up (bind-mounted into router container)
    [ -f tenants.json ] || echo '{"version":1,"tenants":{}}' > tenants.json
    # Prepare per-tenant volume dirs
    for i in $(seq 1 "$TENANT_COUNT"); do
        mkdir -p "volumes/demo$(printf '%02d' "$i")"
    done
    docker compose up -d --build
    info "waiting for all tenant gateways to become ready..."
    for i in $(seq 1 "$TENANT_COUNT"); do
        local c="openclaw-demo$(printf '%02d' "$i")"
        local tries=0
        until docker logs "$c" 2>&1 | grep -q "\[gateway\] ready"; do
            sleep 2
            tries=$((tries + 1))
            [ $tries -ge 60 ] && die "$c didn't report ready in 120s (docker logs $c)"
        done
        ok "$c ready"
    done
}

# ---------- 8. dashscope config + provision ----------
step8_provision() {
    bold "[8/9] Inject Dashscope config + provision tenants"
    cd "$INSTALL_DIR/container-orch"

    for i in $(seq 1 "$TENANT_COUNT"); do
        local name="demo$(printf '%02d' "$i")"
        local cfg="volumes/$name/openclaw.json"
        [ -f "$cfg" ] || die "missing $cfg — container didn't init properly"
        python3 - "$cfg" "$DASHSCOPE_KEY" "$DASHSCOPE_BASE_URL" "$DASHSCOPE_MODEL" <<'PY'
import json, sys
cfg_path, key, base_url, model = sys.argv[1:5]
with open(cfg_path) as f:
    c = json.load(f)
c.setdefault("models", {}).setdefault("providers", {})["dashscope"] = {
    "baseUrl": base_url,
    "apiKey": key,
    "api": "openai-completions",
    "models": [],
}
c.setdefault("agents", {}).setdefault("defaults", {})["model"] = {"primary": f"dashscope/{model}"}
with open(cfg_path, "w") as f:
    json.dump(c, f, indent=2, ensure_ascii=False)
PY
    done
    info "restarting tenants so the new config takes effect"
    docker compose restart $(for i in $(seq 1 "$TENANT_COUNT"); do printf 'openclaw-demo%02d ' "$i"; done)

    info "waiting for tenants to re-ready..."
    for i in $(seq 1 "$TENANT_COUNT"); do
        local c="openclaw-demo$(printf '%02d' "$i")"
        local tries=0
        until [ "$(docker logs "$c" 2>&1 | grep -c '\[gateway\] ready')" -ge 2 ]; do
            sleep 2
            tries=$((tries + 1))
            [ $tries -ge 30 ] && break
        done
    done

    OWUI_ADMIN_EMAIL="$ADMIN_EMAIL" \
    OWUI_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    OWUI_BASE_URL="http://127.0.0.1:$OPENWEBUI_PORT" \
    python3 scripts/provision_demo_tenants.py
    ok "provisioning done"
}

# ---------- 9. summary ----------
step9_summary() {
    bold "[9/9] Done"
    cat <<EOF

╭──────────────────────────────────────────────────────────────╮
│  EasyMultiTenantOpenClaw is live                             │
╰──────────────────────────────────────────────────────────────╯

  OpenWebUI:   http://127.0.0.1:$OPENWEBUI_PORT
  Admin:       $ADMIN_EMAIL / (your password)

  Demo tenant accounts (isolated OpenClaw containers):
    iso-demo01@demo.local  / Demo!Pass01   → openclaw-demo01 :18800
    iso-demo02@demo.local  / Demo!Pass02   → openclaw-demo02 :18801
    iso-demo03@demo.local  / Demo!Pass03   → openclaw-demo03 :18802

  Each demo user sees exactly one model in OpenWebUI, backed by
  a fully isolated OpenClaw container (cron, credentials, skills).

  Code:        $INSTALL_DIR
  Compose:     cd $INSTALL_DIR/container-orch && docker compose ps
  Teardown:    cd $INSTALL_DIR/container-orch && ./scripts/teardown.sh --purge

EOF
}

# ---------- main ----------
main() {
    bold "EasyMultiTenantOpenClaw installer"
    step1_preflight
    step2_clone
    step3_prompt
    step4_openwebui
    step5_admin
    step6_build
    step7_compose_up
    step8_provision
    step9_summary
}

main "$@"
