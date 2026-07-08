FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8005

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /app/state \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8005/ || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8005"]
