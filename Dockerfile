FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[server,anthropic,postgres,mysql]" \
    && useradd --create-home --uid 10001 sqlsentry \
    && mkdir -p /data/.sqlsentry && chown -R sqlsentry /data

COPY examples ./examples
USER sqlsentry
WORKDIR /data

# Sample database so the container works out of the box; mount your own config to replace it.
RUN sqlsentry sample-db /data/store.db
ENV SQLSENTRY_CONFIG=/app/examples/sqlsentry.docker.yaml

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health'); sys.exit(0)"
CMD ["sqlsentry", "serve", "--host", "0.0.0.0", "--port", "8000"]
