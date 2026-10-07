# 本文件构建非 root OAuth 浏览器 worker，不为运行服务发布端口。
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends novnc python3-websockify x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 browser \
    && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin browser

WORKDIR /app
COPY browser-worker/entrypoint.py /app/entrypoint.py
RUN chmod 0555 /app/entrypoint.py

USER 10001:10001
ENTRYPOINT ["python", "/app/entrypoint.py"]
