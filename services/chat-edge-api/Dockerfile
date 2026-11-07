FROM golang:1.25-alpine AS builder
WORKDIR /app
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o chat-edge-api ./cmd/server

FROM alpine:3.20
# python3 + the Office/Excel/Doc libs invoked from scripts/. The
# extractor, inspector, executor, generator, and validator all
# shell out from Go via exec.CommandContext, so the runtime image
# needs the same stack the dev box uses. Pinned via apk so we get
# the alpine-built wheel for openpyxl (avoids a slow pip compile
# on every layer rebuild). build-base lingers only inside the
# install layer so transitive Cython deps can compile, then drops
# out.
#
# Phase 4 Session 2 adds: reportlab (PDF generation) + pypdf
# (cleaner PDF metadata API than pypdfium2 for the inspector).
#
# Phase 13/15: docker-cli is required by the code-interpreter sandbox
# (SANDBOX_ENABLED=1) — internal/sandbox shells out to `docker run` to
# launch each per-exec ai-gateway-sandbox container. Harmless when the
# sandbox is OFF (the default): the CLI just sits unused. Baking it in
# makes `make sandbox-up` turnkey (no manual Dockerfile edit).
RUN apk add --no-cache ca-certificates python3 py3-pip py3-openpyxl py3-lxml docker-cli \
    && apk add --no-cache --virtual .build-deps build-base python3-dev \
    && pip install --no-cache-dir --break-system-packages \
        pdfplumber python-docx reportlab pypdf python-pptx \
    && apk del .build-deps

WORKDIR /app
COPY --from=builder /app/chat-edge-api .
# Python scripts run from /app/scripts — matches ExtractScriptPath
# default ("scripts/extract_text.py") and the Excel script paths
# (scripts/excel_*.py) used by ExcelService.
COPY --from=builder /app/scripts ./scripts
EXPOSE 4000
# FIX126 O6 - provenance: every build carries the commit it was built from.
# compose passes REVISION/CREATED as build args (`REVISION=$(git rev-parse
# --short HEAD) docker compose ... build`); scripts/build-images.sh sets both
# and builds from a clean `git archive` of that commit. Declared at the END
# of the final stage so a new revision never busts the cached layers above.
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED

CMD ["./chat-edge-api"]
