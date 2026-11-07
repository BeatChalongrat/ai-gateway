# web-fetcher — Agent Market's page fetcher (AM12).
#
# Build context = the repository root (it needs the repo's Sarabun fonts):
#   bash scripts/agent-market/build-fetcher.sh   # tags ai-gateway-web-fetcher:<sha>
#
# One URL in, one inert evidence package or one refusal out. A headless
# Chromium (Playwright's own build of the headless shell, pinned by
# package-lock.json) renders the page so that what is read is what a person
# would see; Thai fonts so a Thai page is drawn as itself. Runs as a non-root
# user; tini reaps what a killed browser leaves behind. The image holds no
# key and nothing of any deployment.
FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    NODE_ENV=production \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers

WORKDIR /opt/web-fetcher
COPY services/web-fetcher/package.json services/web-fetcher/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
 && node node_modules/playwright-core/cli.js install --with-deps --only-shell chromium \
 && apt-get install -y --no-install-recommends fonts-thai-tlwg fontconfig tini \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /usr/share/doc/* /usr/share/man/* /root/.npm

COPY services/chat-edge-api/scripts/fonts/Sarabun-Regular.ttf services/chat-edge-api/scripts/fonts/Sarabun-Bold.ttf /usr/share/fonts/truetype/sarabun/
RUN fc-cache -f \
 && useradd --uid 10097 --user-group --home-dir /tmp --no-create-home --shell /usr/sbin/nologin fetcher

COPY services/web-fetcher/src ./src

USER 10097:10097
EXPOSE 8097
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:8097/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED \
      org.opencontainers.image.title="web-fetcher"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "src/server.js"]
