# ComMach: one image serving the API and the web app.

# ---- build the web app
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/ ./
# Remove any node_modules that came with the source, then install fresh.
RUN rm -rf node_modules && npm install --no-audit --no-fund && npm run build

# ---- run the API
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    DATA_DIR=/data
# Fonts with Arabic for labels and printed records.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-noto-core fonts-hosny-amiri \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --create-home --uid 10001 commach && mkdir -p /data && chown commach /data
EXPOSE 8000
# Railway mounts its volume at /data owned by root (no Dockerfile VOLUME allowed): take ownership of the
# volume as root, then drop to the unprivileged commach user for the server itself.
CMD ["sh", "-c", "D=\"${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}\" && mkdir -p \"$D\" && chown commach \"$D\" && HOME=/home/commach exec setpriv --reuid=commach --regid=commach --init-groups uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
