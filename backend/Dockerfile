# Backend: FastAPI + local worker + CLI
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Pango/HarfBuzz for WeasyPrint PDF export
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --extra pdf --extra postgres --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev --extra pdf --extra postgres
COPY config.yaml /app/config.yaml
ENV PATH="/app/backend/.venv/bin:$PATH" DCR_CONFIG=/app/config.yaml DATABASE_URL=sqlite:////data/dcr.db
RUN useradd --create-home --uid 10001 dcr && mkdir -p /data && chown dcr /data
USER dcr
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["dcr", "serve", "--host", "0.0.0.0", "--port", "8000"]
