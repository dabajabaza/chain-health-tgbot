FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first so this layer is cached across source-only changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY alembic.ini ./
COPY migrations/ migrations/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

VOLUME ["/app/data"]

CMD ["python", "-m", "chain_health"]
