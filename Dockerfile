# 备用部署方式（Render 用 render.yaml 走原生 Python 运行时即可，本文件仅在换平台时用）
FROM python:3.11-slim

WORKDIR /app
COPY cloud/requirements.txt /app/cloud/requirements.txt
RUN pip install --no-cache-dir -r /app/cloud/requirements.txt

COPY cloud /app/cloud
ENV PORT=8000
CMD ["sh", "-c", "uvicorn cloud.main:app --host 0.0.0.0 --port ${PORT}"]
