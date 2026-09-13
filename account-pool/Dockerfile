# 本文件构建仅包含号池控制面的运行镜像，Docker CLI 与 Compose 插件内嵌自 digest 锁定的官方镜像。
FROM docker:29-cli@sha256:3f4743208d2338c934d7b8bcfbe1bb54c0b2355c510ad5e0f31c0c4a54bd704e AS docker-cli
FROM ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 AS uv

FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
COPY --from=uv /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
COPY account_pool ./account_pool

RUN uv sync --frozen --no-dev --no-editable --no-cache \
    && mkdir -p /var/lib/litellm-account-pool/environments \
    && chown -R 65532:65532 /var/lib/litellm-account-pool

USER 65532:65532

ENTRYPOINT ["uvicorn", "account_pool.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8091"]
