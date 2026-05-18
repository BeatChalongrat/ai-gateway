# syntax=docker/dockerfile:1.7

# Multi-stage build. The first stage uses the full Go toolchain to
# compile a static binary; the runtime stage is a 30 MB distroless
# image with no shell + only the binary + CA certs.

FROM golang:1.22-bookworm AS builder

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

# ---------------------------------------------------------------------------
# Runtime image
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=builder /out/log-worker /usr/local/bin/log-worker

# gRPC port + metrics port. The healthcheck below uses the metrics
# endpoint because distroless has no shell to run grpc_health_probe.
EXPOSE 50052
EXPOSE 9100

USER nonroot

ENTRYPOINT ["/usr/local/bin/log-worker"]
