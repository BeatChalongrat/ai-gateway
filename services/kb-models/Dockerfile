# kb-models — the knowledge base's local models (FIX132 B).
#   bge-m3             dense 1024-d + sparse lexical weights, one forward pass
#   bge-reranker-v2-m3 cross-encoder reranker
# CPU only. Weights are NOT baked in: they download once into the /models volume at the pinned
# revisions below and load from there; with HF_HUB_OFFLINE=1 the service never reaches out again.
FROM python:3.11-slim

ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED \
      org.opencontainers.image.title="kb-models"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    KB_MODELS_DIR=/models

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir "torch==2.5.1" --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -c "from pythainlp.tokenize import word_tokenize; print(word_tokenize('ทดสอบการตัดคำภาษาไทย', engine='newmm'))"

COPY app /app/app

RUN useradd --create-home --uid 10001 kb && mkdir -p /models && chown kb:kb /models
USER kb

EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=5s --start-period=600s --retries=5 \
  CMD curl -fsS http://localhost:8090/health | grep -q '"status":"ready"' || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1", "--no-access-log"]
