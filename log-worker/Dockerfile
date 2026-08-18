# syntax=docker/dockerfile:1.7

# Multi-stage build. The first stage uses the full Go toolchain to
# compile two static binaries — the gRPC server itself and a tiny
# self-contained healthcheck — and the runtime stage is a 30 MB
# distroless image with no shell + only those two binaries + CA
# certs.

FROM golang:1.25-bookworm AS builder

WORKDIR /src

# Cache module downloads separately from source changes — the layer
# survives every source edit that doesn't touch go.mod / go.sum.
COPY go.mod go.sum* ./
RUN go mod download || true

COPY . .

# CGO disabled → pure Go binary, no glibc link → portable to any
# scratch / distroless image. ``-trimpath`` + ``-ldflags="-s -w"``
# shrink the binary by stripping symbol tables.
RUN CGO_ENABLED=0 GOOS=linux \
    go build -trimpath -ldflags="-s -w" -o /out/log-worker ./

# Healthcheck binary — invoked from HEALTHCHECK below because
# distroless static has no /bin/sh / wget for the CMD-SHELL pattern.
# Shares the toolchain stage so the build artefact ends up in the
# same image layer.
RUN CGO_ENABLED=0 GOOS=linux \
    go build -trimpath -ldflags="-s -w" -o /out/healthcheck ./cmd/healthcheck

# ---------------------------------------------------------------------------
# Runtime image
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=builder /out/log-worker /usr/local/bin/log-worker
# Healthcheck lives at the root so the docker-compose ``test``
# command is the shortest possible path (no shell to resolve PATH).
COPY --from=builder /out/healthcheck /healthcheck

# gRPC port + metrics port. The Dockerfile-level HEALTHCHECK uses
# the binary above so Docker can verify the container is live even
# without a shell. docker-compose.yml may re-declare ``healthcheck``
# to override interval/retries, which takes precedence.
EXPOSE 50052
EXPOSE 9100

USER nonroot

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
    CMD ["/healthcheck"]

ENTRYPOINT ["/usr/local/bin/log-worker"]
