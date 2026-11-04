# 构建预装 Camoufox 的非 root Linux worker，只通过原有回调和屏幕中继访问。
ARG TARGETARCH
FROM public.ecr.aws/docker/library/node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS automation
WORKDIR /automation
COPY browser-worker/automation/package*.json ./
RUN npm ci --ignore-scripts --omit=dev --registry=https://registry.npmjs.org

FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS browser-download-amd64
ADD --checksum=sha256:3f2eb1d0451fa58e45e43e55f5e6181565069791ae60c0574f0ceb1f01341c33 https://github.com/daijro/camoufox/releases/download/v158.0.1-beta.41/camoufox-158.0.1-beta.41-lin.x86_64.zip /browser.zip

FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS browser-download-arm64
ADD --checksum=sha256:60e25f6eb95e13d9ec655bc02913d51057e669cb31542e6ace9d2190ab5380af https://github.com/daijro/camoufox/releases/download/v158.0.1-beta.41/camoufox-158.0.1-beta.41-lin.arm64.zip /browser.zip

FROM browser-download-${TARGETARCH} AS browser-download
RUN python -c "import zipfile; z=zipfile.ZipFile('/browser.zip'); z.extractall('/opt/camoufox', members=[n for n in z.namelist() if not n.startswith(('fonts/M/', 'fonts/W/'))])" \
    && chmod -R a+rX /opt/camoufox && chmod 0555 /opt/camoufox/camoufox-bin

FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates novnc python3-websockify x11vnc xvfb \
       libgtk-3-0 libdbus-glib-1-2 libasound2 libx11-xcb1 libxt6 libpci3 \
       fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 browser \
    && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin browser

COPY browser-worker/requirements.lock /tmp/requirements.lock
RUN python -m pip install --index-url https://pypi.org/simple --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.lock \
    && python -c "from camoufox.fpgen_model import load_fpgen; load_fpgen(); from camoufox.fingerprints import generate_fingerprint; generate_fingerprint(os='linux')" \
    && rm /tmp/requirements.lock

COPY --from=automation /usr/local/bin/node /usr/local/bin/node
COPY --from=automation /automation/node_modules /app/automation/node_modules
COPY --from=browser-download /opt/camoufox /opt/camoufox

WORKDIR /app
COPY browser-worker/entrypoint.py /app/entrypoint.py
COPY browser-worker/camoufox_profile.py /app/camoufox_profile.py
COPY browser-worker/automation/*.mjs /app/automation/
RUN chmod 0555 /app/entrypoint.py /app/camoufox_profile.py

USER 10001:10001
ENTRYPOINT ["python", "/app/entrypoint.py"]
