# openclaw:base — single-tenant OpenClaw gateway in a container.
#
# All tenant state (openclaw.json, cron/, credentials/, exec-approvals,
# sessions, memory, ...) lives under /data which is mounted as a volume.
# One container = one tenant. Spawn N of these, each with its own volume
# and external port, to get hard cron/credentials/exec isolation.

FROM node:22-bookworm-slim

# Keep the image lean: install what npm postinstall hooks commonly need,
# plus ca-certs for HTTPS to the LLM provider.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git python3 \
    && rm -rf /var/lib/apt/lists/*

# Pin to the exact version the host is using, so skill compatibility
# matches what we've already validated.
RUN npm install -g --no-fund --no-audit \
      openclaw@2026.4.7 \
      clawhub@0.9.0

# Fix: bundled channel plugins ship their own node_modules under
# dist/extensions/<name>/node_modules/ but the gateway's request handler
# resolves them from the package root. Symlinking every extension dep
# to the parent node_modules/ bridges the gap without extra downloads.
COPY link-extension-deps.sh /tmp/link-extension-deps.sh
RUN chmod +x /tmp/link-extension-deps.sh && /tmp/link-extension-deps.sh && rm /tmp/link-extension-deps.sh

# Tenant state root. Volume mount point — one physical dir per tenant on host.
ENV OPENCLAW_STATE_DIR=/data \
    OPENCLAW_CONFIG_PATH=/data/openclaw.json \
    HOME=/data

VOLUME /data
WORKDIR /data

# Container-internal gateway port. Compose maps each container to its own
# external port (18800, 18801, ...).
EXPOSE 18789

# Bootstrap + exec wrapper. On first boot this seeds /data/openclaw.json
# with a random auth token; on subsequent boots it just execs the gateway.
# The token is printed to stdout so `docker logs <container>` can surface
# it for the provisioning script.
COPY start-openclaw.sh /usr/local/bin/start-openclaw.sh
RUN chmod +x /usr/local/bin/start-openclaw.sh
ENTRYPOINT ["/usr/local/bin/start-openclaw.sh"]
