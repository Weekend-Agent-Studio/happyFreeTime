FROM python:3.12-slim-bookworm AS dependencies

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

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM dependencies AS runtime
COPY app/ ./app/
COPY data/catalog/ ./data/catalog/
COPY data/demo_world/ ./data/demo_world/
COPY data/demo_showcases/ ./data/demo_showcases/
COPY data/fixtures/ ./data/fixtures/
COPY data/retrieval/v1/ ./data/retrieval/v1/
COPY deploy/backup_volume.py ./deploy/backup_volume.py
COPY deploy/smoke.py ./deploy/smoke.py

ARG HFT_REVISION=development
LABEL org.opencontainers.image.source="https://github.com/Weekend-Agent-Studio/happyFreeTime" \
      org.opencontainers.image.revision="${HFT_REVISION}"

# CPU-only optional image. Assets are prepared on the developer's computer,
# transferred separately from Git, and never downloaded at application startup.
FROM dependencies AS rag_dependencies
USER root
COPY requirements-rag.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 \
    && pip install --no-cache-dir -r requirements-rag.txt && pip check

# Neither application code, release metadata nor model assets invalidate the
# expensive CPU dependency stage above.
FROM rag_dependencies AS rag
COPY --from=runtime /app/ /app/
COPY deploy/retrieval/model/ /opt/hft-retrieval/model/
COPY deploy/retrieval/index/ /opt/hft-retrieval/index/
COPY deploy/retrieval/asset_manifest.json /opt/hft-retrieval/asset_manifest.json
RUN chmod -R a+rX /opt/hft-retrieval
ENV HFT_EMBEDDING_MODEL_PATH=/opt/hft-retrieval/model \
    HFT_RETRIEVAL_CACHE_DIR=/opt/hft-retrieval/index \
    HFT_EMBEDDING_DEVICE=cpu \
    HFT_CANDIDATE_RETRIEVER_MODE=hybrid \
    HFT_RETRIEVAL_PREWARM=true \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false
USER 10001:10001
ARG HFT_REVISION=development
LABEL org.opencontainers.image.source="https://github.com/Weekend-Agent-Studio/happyFreeTime" \
      org.opencontainers.image.revision="${HFT_REVISION}"
