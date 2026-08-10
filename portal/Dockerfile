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
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3001
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3001
CMD ["node", "server.js"]
