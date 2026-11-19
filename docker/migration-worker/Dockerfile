# Separate service: not a substitute for a VM sandbox against hostile code.
FROM node:22-bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates && rm -rf /var/lib/apt/lists/*
RUN npm install --global @openai/codex@0.170.0
WORKDIR /service
COPY backend/requirements.txt /service/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /service/requirements.txt
COPY backend/app /service/app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 LEGACYLIFT_MIGRATION_WORKER_MODE=separate LEGACYLIFT_DATA_DIR=/workspace/data
CMD ["python", "-m", "app.migration_worker"]
