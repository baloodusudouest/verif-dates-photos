# syntax=docker/dockerfile:1
# =============================================================================
# verif-dates-photos — image multi-stage
#   1. builder : installe les paquets Python dans un environnement virtuel
#   2. final   : Python slim + exiftool + ffmpeg, sans outils de compilation
# =============================================================================

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt

FROM python:3.12-slim
ARG VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/baloodusudouest/verif-dates-photos" \
      org.opencontainers.image.description="Vérification des dates des photos avant migration vers Immich" \
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
WORKDIR /app
COPY app/ /app/
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8099
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8099\")}/health', timeout=5)"
CMD ["python", "/app/app.py"]
