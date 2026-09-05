# syntax=docker/dockerfile:1.7

# Multi-stage Rust build. Stage 1 uses the official rust:1.78 image
# plus protoc (required by tonic-build); stage 2 copies the two
# release binaries into distroless/cc — the smallest image that
# still ships glibc (the redis crate's TLS pulls in libssl which
# wants glibc; static-mussl would require switching the crate
# features and isn't worth the headache for this sidecar).

FROM rust:1.86-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        protobuf-compiler pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Cargo manifest first so the dependency layer is cached between
# source edits. We seed dummy source files so ``cargo build`` has
# something to compile against, then overwrite + rebuild with the
# real code.
# FIX63 — build from the tracked Cargo.lock, never from a fresh resolution
# (see dlp-sidecar/Dockerfile for the day this bit).
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src cmd/healthcheck proto \
    && echo "fn main() {}" > src/main.rs \
    && echo "fn main() {}" > cmd/healthcheck/main.rs \
    && touch proto/.placeholder \
    && cargo fetch --locked

COPY proto/ ./proto/
COPY build.rs ./
COPY src/ ./src/
COPY cmd/ ./cmd/

# Build both binaries in release mode.
RUN cargo build --locked --release --bin rate-limiter --bin healthcheck

# ---------------------------------------------------------------------------
# Runtime image
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/cc-debian12:nonroot

COPY --from=builder /src/target/release/rate-limiter /usr/local/bin/rate-limiter
COPY --from=builder /src/target/release/healthcheck  /healthcheck

EXPOSE 50053
EXPOSE 9101

USER nonroot

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=15s \
    CMD ["/healthcheck"]

ENTRYPOINT ["/usr/local/bin/rate-limiter"]
