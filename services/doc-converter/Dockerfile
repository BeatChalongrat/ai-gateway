# doc-converter — Agent Market's LibreOffice conversion service (AM-2 Phase E).
#
# Build context = the repository root (it needs the repo's Sarabun fonts):
#   bash scripts/agent-market/build-converter.sh   # tags ai-gateway-doc-converter:<sha>
#
# A finished .docx/.xlsx/.pptx becomes a PDF by a real LibreOffice
# conversion; an .xlsx comes back recalculated. Headless LibreOffice (the
# -nogui builds of writer/calc/impress: no X, GTK or Java), poppler for the
# PDF check, Thai fonts (TLWG + Sarabun) and metric-compatible substitutes
# for the Office defaults, so a customer template keeps its page breaks.
# Runs as a non-root user; tini reaps what a killed soffice leaves behind.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      poppler-utils \
      fonts-thai-tlwg fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 \
      fontconfig python3 tini \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /usr/share/doc/* /usr/share/man/*

COPY services/chat-edge-api/scripts/fonts/Sarabun-Regular.ttf services/chat-edge-api/scripts/fonts/Sarabun-Bold.ttf /usr/share/fonts/truetype/sarabun/
COPY services/doc-converter/fontconfig/65-sran-thai.conf /etc/fonts/conf.d/65-sran-thai.conf
RUN fc-cache -f \
 && useradd --uid 10096 --user-group --home-dir /tmp --no-create-home --shell /usr/sbin/nologin converter

COPY services/doc-converter/server.py /opt/doc-converter/server.py

USER 10096:10096
EXPOSE 8096
HEALTHCHECK --interval=15s --timeout=4s --start-period=30s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8096/health', timeout=3).status == 200 else 1)"
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED \
      org.opencontainers.image.title="doc-converter"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/doc-converter/server.py"]
