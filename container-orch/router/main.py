"""
openclaw-router — routes OpenWebUI requests to per-user OpenClaw containers.

Listens on 127.0.0.1:18888 (configurable via ROUTER_PORT env).

Request flow:
  1. OpenWebUI sends POST /v1/chat/completions (or GET /v1/models, etc.)
     with header X-OpenWebUI-User-Id set by ENABLE_FORWARD_USER_INFO_HEADERS.
  2. Router reads that header, looks up tenants.json for the user's
     container port and gateway token.
  3. Forwards the request to http://127.0.0.1:{port}/v1/... with the
     tenant's token, streaming the response back.

Run:
  cd router && pip install -r requirements.txt
  uvicorn main:app --host 127.0.0.1 --port 18888
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

import tenants

log = logging.getLogger("openclaw-router")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="openclaw-router", version="0.1.0")

# A single persistent client for all forwarded requests.
_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=300, write=10, pool=5))

USER_ID_HEADER = os.getenv(
    "FORWARD_USER_INFO_HEADER_USER_ID", "X-OpenWebUI-User-Id"
).lower()


@app.get("/health")
def health():
    return {"ok": True, "tenants": len(tenants.all_tenants())}


@app.get("/v1/models")
async def models(request: Request):
    """Return a generic models list.

    OpenWebUI calls GET /v1/models on each connection to discover base
    models — this happens *without* X-OpenWebUI-User-Id (it's a
    connection-level probe, not a user-level request).  We return a
    static list so OpenWebUI can register the base model ids that
    workspace models point to.

    When a real user triggers a chat, the POST goes through the
    proxy() handler below which *does* require the user header.
    """
    # Pick any tenant to proxy to (all containers expose the same
    # model ids — openclaw, openclaw/default, openclaw/main).
    all_t = tenants.all_tenants()
    if not all_t:
        return JSONResponse(content={"object": "list", "data": []})
    first = next(iter(all_t.values()))
    port, token = first["port"], first["gateway_token"]
    resp = await _client.get(
        f"http://127.0.0.1:{port}/v1/models",
        headers={"Authorization": f"Bearer {token}"},
    )
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(rest: str, request: Request):
    # 1. Identify calling user
    user_id = request.headers.get(USER_ID_HEADER)
    if not user_id:
        return JSONResponse(
            status_code=400,
            content={"error": f"missing {USER_ID_HEADER} header"},
        )

    # 2. Lookup tenant
    tenant = tenants.lookup(user_id)
    if tenant is None:
        log.warning("no tenant for user_id=%s", user_id)
        return JSONResponse(
            status_code=404,
            content={"error": "no tenant for this user"},
        )

    port = tenant["port"]
    token = tenant["gateway_token"]
    url = f"http://127.0.0.1:{port}/v1/{rest}"

    # 3. Build forwarded headers (replace auth, drop hop-by-hop)
    fwd_headers = {}
    for k, v in request.headers.items():
        kl = k.lower()
        if kl in ("host", "authorization", "connection", "transfer-encoding"):
            continue
        fwd_headers[k] = v
    fwd_headers["Authorization"] = f"Bearer {token}"

    body = await request.body()
    method = request.method

    log.info("→ %s /v1/%s  user=%s  tenant=%s:%s", method, rest, user_id[:8], tenant["container"], port)

    # 4. Forward — detect streaming by Accept or body hints
    wants_stream = (
        "text/event-stream" in request.headers.get("accept", "")
        or b'"stream":true' in body
        or b'"stream": true' in body
    )

    if wants_stream:
        req = _client.build_request(method, url, headers=fwd_headers, content=body)
        upstream = await _client.send(req, stream=True)

        async def event_generator():
            try:
                async for chunk in upstream.aiter_bytes(1024):
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            event_generator(),
            status_code=upstream.status_code,
            headers=dict(upstream.headers),
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )
    else:
        resp = await _client.request(method, url, headers=fwd_headers, content=body)
        return JSONResponse(
            status_code=resp.status_code,
            content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text},
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("ROUTER_PORT", "18888"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
