# 本镜像仅给固定版本 FreeBuff 加载出站代理适配器，不改动上游业务代码。
FROM pingmike/freebuff2api@sha256:52e511ed7a64d8198edfb8e4e93c4b1ad1ad581b34b7b1765c7e42ceeed3d779

USER root
WORKDIR /opt/freebuff-proxy
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY proxy-bootstrap.mjs ./

WORKDIR /app
USER node
ENTRYPOINT ["node", "--import", "/opt/freebuff-proxy/proxy-bootstrap.mjs", "/app/server.js"]
