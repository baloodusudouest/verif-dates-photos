# syntax=docker/dockerfile:1
# =============================================================================
# Éphéméride (verif-dates-photos) — image multi-stage, format commun du socle (3.0.0)
#   1. interface : Svelte 5 + Tailwind 4 compilés par Vite
#   2. builder   : paquets Python (FastAPI, Uvicorn, Pillow…) dans un environnement virtuel
#   3. final     : Python slim + exiftool + ffmpeg, sans outils de compilation
# L'outil tourne en root, comme avant : il modifie les fichiers de la Pellicule et doit garder
# leur propriétaire tel quel (pas de gosu ici, contrairement au socle).
# =============================================================================

FROM node:22-bookworm-slim AS interface
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p /app && npx vite build --outDir /app/static --emptyOutDir

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt

FROM python:3.12-slim
ARG VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/baloodusudouest/verif-dates-photos" \
      org.opencontainers.image.description="Éphéméride : vérification des dates des photos avant migration vers Immich" \
      org.opencontainers.image.version="${VERSION}"
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:$PATH" \
    DATA_DIR=/data \
    PORT=8099
RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /venv /venv
WORKDIR /srv
COPY app/ /srv/app/
COPY --from=interface /app/static /srv/app/static
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8099
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8099\")}/health', timeout=5)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8099}\" --proxy-headers --no-access-log"]
