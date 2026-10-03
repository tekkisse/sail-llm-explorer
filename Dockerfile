# One image for every component; the command selects which one runs.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MCP_HOST=0.0.0.0 API_HOST=0.0.0.0

RUN useradd --uid 10001 --create-home app
WORKDIR /app

COPY pyproject.toml README.md ./
COPY sailx ./sailx
RUN pip install ".[ui]"

COPY ui ./ui
COPY demo ./demo
COPY chainlit.md ./
RUN chown -R app:app /app
USER 10001

# Default: the web UI. Override with sailx-sql-mcp, sailx-phenotype-mcp, sailx-catalog-mcp, sailx-agent-api.
EXPOSE 8000
CMD ["chainlit", "run", "ui/app.py", "--host", "0.0.0.0", "--port", "8000", "--headless"]
