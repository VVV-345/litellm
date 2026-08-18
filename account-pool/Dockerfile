# 本镜像复用同一次 Compose 构建出的 LiteLLM Python 运行环境，只加入独立号池模块。
FROM litellm_image

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY account-pool/account_pool ./account_pool

EXPOSE 4100

ENTRYPOINT ["/app/.venv/bin/python", "-m", "uvicorn"]
CMD ["account_pool.app:app", "--host", "0.0.0.0", "--port", "4100"]
