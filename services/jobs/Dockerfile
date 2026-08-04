# Build desde la raíz del repo: docker build -f services/jobs/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv sync --frozen --no-dev --package ordo-jobs

FROM python:3.12-slim
RUN useradd --system --uid 10001 ordo
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services/jobs /app/services/jobs
ENV PATH="/app/.venv/bin:$PATH"
USER ordo
EXPOSE 8000
CMD ["uvicorn", "ordo_jobs.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
