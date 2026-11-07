FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_OAUTH_ENABLED
ENV NEXT_PUBLIC_OAUTH_ENABLED=$NEXT_PUBLIC_OAUTH_ENABLED
# Where SRAN Chat lives. Read by FOUR call sites — the dashboard "Open SRAN
# Chat" link (used verbatim as the href) and three open-redirect allowlists
# (middleware.ts, LoginForm, oauth-complete — each takes .origin of it, so a
# path suffix is harmless). All four already read this var; it was simply
# never supplied, and NEXT_PUBLIC_* is inlined at BUILD time, so every image
# baked in the localhost:3002 fallback and the portal→chat handoff broke on
# any real hostname. Must be an ARG, not just runtime env.
ARG NEXT_PUBLIC_WEB_CHAT_URL
ENV NEXT_PUBLIC_WEB_CHAT_URL=$NEXT_PUBLIC_WEB_CHAT_URL
# NEXTAUTH_SECRET is a RUNTIME server-side secret (getToken() reads
# process.env when a request comes in) — compose `environment:` is the
# authoritative source and overrides this ENV. The ARG/ENV pair exists
# only so a bare `docker run` without compose can still inject it at
# build; DO NOT pass a real secret as a build arg in CI/prod — build
# args persist in image history. Leave it unset here.
ARG NEXTAUTH_SECRET
ENV NEXTAUTH_SECRET=$NEXTAUTH_SECRET
# FIX134 A2 — where next.config.js rewrites /api and /static to. With
# `output: 'standalone'` the rewrite destinations are written into the build
# (routes manifest), so this is a BUILD-time value: the compose `environment:`
# twin never reached the rewrites, and every image rewrote to the
# http://localhost:8080 fallback inside the container (/api/health 500,
# /api/portal/me 503, ECONNREFUSED). compose passes it from
# PORTAL_BACKEND_URL; the default is the compose service name.
ARG BACKEND_URL=http://backend:8080
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3001
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3001
# FIX126 O6 - provenance: every build carries the commit it was built from.
# compose passes REVISION/CREATED as build args (`REVISION=$(git rev-parse
# --short HEAD) docker compose ... build`); scripts/build-images.sh sets both
# and builds from a clean `git archive` of that commit. Declared at the END
# of the final stage so a new revision never busts the cached layers above.
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED

CMD ["node", "server.js"]
