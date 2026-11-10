# Hugging Face Docker Space for the sqlsentry playground.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Spaces run the container as uid 1000.
RUN useradd --create-home --uid 1000 user
WORKDIR /app

# Installs from the GitHub repo (no git needed); switch to "sqlsentry[server]" once on PyPI.
RUN pip install "sqlsentry[server] @ https://github.com/2002sairuthvik/sqlsentry/archive/refs/heads/main.zip"

COPY --chown=user sqlsentry.yaml /app/sqlsentry.yaml
USER user
RUN sqlsentry sample-db /app/store.db

EXPOSE 7860
CMD ["sqlsentry", "serve", "--config", "/app/sqlsentry.yaml", "--host", "0.0.0.0", "--port", "7860"]
