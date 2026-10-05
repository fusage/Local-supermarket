# 毎朝の自動収集（scripts/collect.py）を Cloud Run ジョブで動かすためのコンテナ
#   デプロイ: gcloud run jobs deploy collect --source . （手順は docs/gcp.md）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Tokyo

WORKDIR /app
COPY requirements-collect.txt .
RUN pip install -r requirements-collect.txt

COPY lib ./lib
COPY scripts ./scripts
COPY data/csv ./data/csv

CMD ["python", "scripts/collect.py"]
