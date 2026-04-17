# Router image — dispatches OpenWebUI requests to per-tenant OpenClaw
# containers based on X-OpenWebUI-User-Id. Reads tenants.json at /app.

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --no-compile \
      "fastapi>=0.115" "uvicorn>=0.34" "httpx>=0.28"

COPY main.py tenants.py ./

EXPOSE 18888

ENV ROUTER_PORT=18888 \
    TENANTS_FILE=/app/tenants.json

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18888"]
