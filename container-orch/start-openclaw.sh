#!/bin/sh
# Container entrypoint: bootstraps /data/openclaw.json on first launch,
# then execs the gateway.
#
# On first boot the volume is empty, so we seed it with a minimal config:
#   - mode=local        (no remote bridge, single-host use)
#   - port=18789        (container-internal; compose maps to host port)
#   - bind=lan          (listens on non-loopback; required for port mapping
#                        to reach from outside the container)
#   - auth.mode=token   (a random token we print to stdout for the router
#                        to pick up via `docker logs`)
#   - chatCompletions   (OpenAI-compat endpoint required for OpenWebUI)
#
# On subsequent boots the config already exists and we just start the
# gateway, so restarts / upgrades don't rotate the token or lose state.

set -e

CFG=/data/openclaw.json

if [ ! -f "$CFG" ]; then
    # /proc/sys/kernel/random/uuid is always present on Linux; stripping
    # dashes gives us a 32-char hex token with ~128 bits of entropy.
    TOKEN="$(cat /proc/sys/kernel/random/uuid | tr -d -)"

    openclaw config set gateway.mode local                               >/dev/null
    openclaw config set gateway.port 18789                               >/dev/null
    openclaw config set gateway.bind lan                                 >/dev/null
    openclaw config set gateway.auth.mode token                          >/dev/null
    openclaw config set gateway.auth.token "$TOKEN"                      >/dev/null
    openclaw config set gateway.http.endpoints.chatCompletions.enabled true >/dev/null

    # Marker log line for the provisioning script to grep from docker logs.
    echo "[openclaw-init] bootstrapped config; gateway.auth.token=$TOKEN"
fi

exec openclaw gateway
