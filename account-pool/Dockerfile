ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a

FROM $UV_IMAGE AS uvbin
FROM litellm_image

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=uvbin /uv /uvx /usr/local/bin/

WORKDIR /app/account-pool

COPY account-pool/pyproject.toml account-pool/uv.lock ./
COPY account-pool/account_pool ./account_pool

RUN uv sync --project /app/account-pool --frozen --no-dev --no-editable \
    && /app/account-pool/.venv/bin/python -c "import psycopg"

EXPOSE 4100

ENTRYPOINT ["/app/account-pool/.venv/bin/python", "-m", "uvicorn"]
CMD ["account_pool.app:app", "--host", "0.0.0.0", "--port", "4100"]
