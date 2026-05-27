FROM golang:1.24-alpine AS builder
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
RUN apk add --no-cache ca-certificates python3 py3-pip py3-openpyxl py3-lxml \
    && apk add --no-cache --virtual .build-deps build-base python3-dev \
    && pip install --no-cache-dir --break-system-packages \
        pdfplumber python-docx reportlab pypdf \
    && apk del .build-deps

WORKDIR /app
COPY --from=builder /app/chat-edge-api .
# Python scripts run from /app/scripts — matches ExtractScriptPath
# default ("scripts/extract_text.py") and the Excel script paths
# (scripts/excel_*.py) used by ExcelService.
COPY --from=builder /app/scripts ./scripts
EXPOSE 4000
CMD ["./chat-edge-api"]
