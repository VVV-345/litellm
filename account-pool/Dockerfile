# 本文件构建与 LiteLLM 一同运行的独立号池服务镜像。
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a

FROM $UV_IMAGE AS uvbin
FROM litellm_image

# account-pool-postgres 备份 CLI 需要 pg_dump/pg_restore，版本须与 postgres:16 服务端一致。
USER root
RUN apk add --no-cache postgresql-16-client \
    && test -x "$(command -v pg_dump)" \
    && test -x "$(command -v pg_restore)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=uvbin /uv /uvx /usr/local/bin/

WORKDIR /app/account-pool

COPY account-pool/pyproject.toml account-pool/uv.lock ./
COPY account-pool/account_pool ./account_pool

RUN uv sync --project /app/account-pool --frozen --no-dev --no-editable \
    && /app/account-pool/.venv/bin/python -c "import psycopg"

# 确保非特权用户能写入数据挂载点；named volume 首次创建时继承镜像内的权限。
RUN mkdir -p /data /data/parser-snapshots && chown -R nonroot:nonroot /data
# 运行时使用基础镜像自带的非特权用户；/data 挂载点由 compose 侧卷负责可写。
USER nonroot

EXPOSE 4100

ENTRYPOINT ["/app/account-pool/.venv/bin/python", "-m", "uvicorn"]
CMD ["account_pool.app:app", "--host", "0.0.0.0", "--port", "4100"]
