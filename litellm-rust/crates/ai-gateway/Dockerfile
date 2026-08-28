# Builds the Rust gateway against the same Python runtime used by LiteLLM.

ARG RUST_BUILD_IMAGE=cgr.dev/chainguard/rust@sha256:e030496d41dda422a1feb29a2f39e1271a6a8b34b43267ce1c3e012a20092e3b

FROM litellm_image AS python-runtime

FROM ${RUST_BUILD_IMAGE} AS builder

USER root

# PyO3 links against the Python ABI that the LiteLLM runtime will use.
COPY --from=python-runtime /usr/bin/python3 /usr/bin/python3
COPY --from=python-runtime /usr/lib/libpython3.13.so.1.0 /usr/lib/libpython3.13.so.1.0
COPY --from=python-runtime /usr/lib/python3.13 /usr/lib/python3.13
COPY --from=python-runtime /usr/include/python3.13 /usr/include/python3.13

ENV PYO3_PYTHON=/usr/bin/python3

RUN ln -s libpython3.13.so.1.0 /usr/lib/libpython3.13.so \
    && python3 --version \
    && rustc --version \
    && cargo --version

WORKDIR /build/litellm-rust

COPY litellm-rust/ ./

RUN cargo build --locked --release -p litellm-ai-gateway --features server,python-config

FROM litellm_image AS runtime

USER root

WORKDIR /app

COPY --from=builder /build/litellm-rust/target/release/litellm-ai-gateway /usr/local/bin/litellm-ai-gateway
COPY litellm-rust/crates/ai-gateway/config.yaml /app/config.yaml

ENV HOST=0.0.0.0 \
    LITELLM_CONFIG_PATH=/app/config.yaml \
    PYTHONDONTWRITEBYTECODE=1

USER nonroot

ENTRYPOINT ["/usr/local/bin/litellm-ai-gateway"]
