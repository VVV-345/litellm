# 本文件构建仅包含号池控制面的运行镜像，Docker Socket 由部署层按需挂载。
FROM python:3.13.7-slim-bookworm

WORKDIR /app

COPY pyproject.toml README.md ./
COPY account_pool ./account_pool

RUN pip install --no-cache-dir .

ENTRYPOINT ["uvicorn", "account_pool.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8091"]
