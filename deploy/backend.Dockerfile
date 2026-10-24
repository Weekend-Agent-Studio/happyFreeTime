FROM python:3.12-slim-bookworm

ARG HFT_REVISION=development
LABEL org.opencontainers.image.source="https://github.com/Weekend-Agent-Studio/happyFreeTime" \
      org.opencontainers.image.revision="${HFT_REVISION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    TZ=Asia/Shanghai
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip check \
    && groupadd --gid 10001 hft \
    && useradd --uid 10001 --gid 10001 --no-create-home hft \
    && mkdir -p /var/lib/happyfreetime \
    && chown 10001:10001 /var/lib/happyfreetime

COPY app/ ./app/
COPY data/catalog/ ./data/catalog/
COPY data/demo_world/ ./data/demo_world/
COPY data/demo_showcases/ ./data/demo_showcases/
COPY data/fixtures/ ./data/fixtures/
COPY deploy/backup_volume.py ./deploy/backup_volume.py
COPY deploy/smoke.py ./deploy/smoke.py

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
