# syntax=docker/dockerfile:1.7

# Multi-stage Rust build — same pattern as rate-limiter. Stage 1
# compiles two release binaries with the full toolchain + protoc;
# stage 2 is distroless/cc-debian12 with the binaries + healthcheck.

FROM rust:1.86-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        protobuf-compiler pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# FIX63 — build from the tracked Cargo.lock, never from a fresh resolution.
# Without the lockfile every image build picked whatever the registry had
# that day; on 2026-09-05 that was icu_* 2.3.0, which needs rustc 1.88 on a
# 1.86 builder, and the image could not be built at all.
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src cmd/healthcheck proto \
    && echo "fn main() {}" > src/main.rs \
    && echo "fn main() {}" > cmd/healthcheck/main.rs \
    && touch proto/.placeholder \
    && cargo fetch --locked

COPY proto/ ./proto/
COPY build.rs ./
COPY src/ ./src/
# FIX29: the Thai name/word lexicons are embedded at build time (include_str!)
COPY data/ ./data/
COPY cmd/ ./cmd/

RUN cargo build --locked --release --bin dlp-sidecar --bin healthcheck

# ---------------------------------------------------------------------------
# Runtime image
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/cc-debian12:nonroot

COPY --from=builder /src/target/release/dlp-sidecar /usr/local/bin/dlp-sidecar
COPY --from=builder /src/target/release/healthcheck /healthcheck

EXPOSE 50051
EXPOSE 9102

USER nonroot

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=30s \
    CMD ["/healthcheck"]

ENTRYPOINT ["/usr/local/bin/dlp-sidecar"]
