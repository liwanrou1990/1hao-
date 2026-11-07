FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pyarrow duckdb httpx pyyaml
COPY . .
ENV PYTHONPATH=src TZ=UTC PYTHONUNBUFFERED=1
HEALTHCHECK --interval=15m --timeout=60s --retries=2 CMD python -m brando_pa.cli forward-health || exit 1
CMD ["python", "-m", "brando_pa.cli", "forward-service"]
