# agent-runner — Agent Market's execution plane (docs/agent-market/).
#
# Build context = the repository root (it needs the Go module AND the
# Python engine in backend/agent_market):
#   bash scripts/agent-market/build.sh            # tags ai-gateway-agent-runner:<sha>
#
# The image holds the Go binary, the docker CLI (steps run in the stock
# ai-gateway-sandbox image, one fresh container per step) and the engine as
# ONE deterministic zip — the same bytes every sandbox step receives and the
# package checksum is computed from.
FROM golang:1.25-alpine AS builder
WORKDIR /src
COPY services/chat-edge-api/go.mod services/chat-edge-api/go.sum ./
RUN go mod download
COPY services/chat-edge-api/ ./
ARG REVISION=unknown
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags "-X main.version=${REVISION}" -o /out/agent-runner ./cmd/agent-runner
COPY backend/agent_market/ /engine-src/agent_market/
RUN /out/agent-runner --build-engine-zip /engine-src/agent_market /out/agent_market.zip

FROM alpine:3.20
RUN apk add --no-cache ca-certificates docker-cli wget tzdata
WORKDIR /app
COPY --from=builder /out/agent-runner /app/agent-runner
COPY --from=builder /out/agent_market.zip /app/agent_market.zip
ENV AGENT_MARKET_ENGINE_ZIP=/app/agent_market.zip \
    AGENT_RUNNER_ADDR=:8095
EXPOSE 8095
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8095/healthz || exit 1
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED \
      org.opencontainers.image.title="agent-runner"
CMD ["/app/agent-runner"]
