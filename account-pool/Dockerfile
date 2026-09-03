# 本文件构建仅包含号池控制面的运行镜像，Docker CLI 与 Compose 插件内嵌自 digest 锁定的官方镜像。
FROM docker:29-cli@sha256:3f4743208d2338c934d7b8bcfbe1bb54c0b2355c510ad5e0f31c0c4a54bd704e AS docker-cli

FROM python:3.13.7-slim-bookworm

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose

WORKDIR /app

COPY pyproject.toml README.md ./
COPY account_pool ./account_pool

RUN pip install --no-cache-dir . \
    && mkdir -p /var/lib/litellm-account-pool/environments \
    && chown -R 65532:65532 /var/lib/litellm-account-pool

USER 65532:65532

ENV PATH="/usr/local/bin:${PATH}"

ENTRYPOINT ["uvicorn", "account_pool.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8091"]
