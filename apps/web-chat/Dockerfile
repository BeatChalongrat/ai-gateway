# --- builder -------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app

# Install deps separately so the layer caches.
COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .

# NEXT_PUBLIC_* is inlined into the browser bundle by the bundler AT BUILD
# TIME — a value supplied only as a container `environment:` entry never
# reaches client code. It must be present HERE, before `npm run build`, or
# lib/api.ts falls back to its hardcoded http://localhost:4000/api/v1 and
# every non-localhost deploy talks to the wrong host.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# Same build-time rule for the auth endpoints (SSO Phase 1, D1):
# lib/auth.ts reads NEXT_PUBLIC_GATEWAY_URL (session lookup /portal/me,
# silent refresh, logout) and NEXT_PUBLIC_PORTAL_URL (login/signup
# redirect). Neither was ever passed, so every non-localhost image baked
# in http://localhost:8080 / :3001 and auth broke off-box.
ARG NEXT_PUBLIC_GATEWAY_URL
ENV NEXT_PUBLIC_GATEWAY_URL=$NEXT_PUBLIC_GATEWAY_URL
ARG NEXT_PUBLIC_PORTAL_URL
ENV NEXT_PUBLIC_PORTAL_URL=$NEXT_PUBLIC_PORTAL_URL
# FIX56.1 — what the temporary-chat strip says about saved memories.
# Only an explicit "false" flips it, so an unset arg keeps the default
# rather than baking in the stricter claim. Must match chat-edge's
# TEMPORARY_CHAT_READS_MEMORY.
ARG NEXT_PUBLIC_TEMPORARY_CHAT_READS_MEMORY
ENV NEXT_PUBLIC_TEMPORARY_CHAT_READS_MEMORY=$NEXT_PUBLIC_TEMPORARY_CHAT_READS_MEMORY
# (FIX20 §6.5: hosted mode — the desktop shell — is no longer a build arg.
# lib/auth.ts detects the shell at runtime from window.sranHost, so ONE image
# serves the browser and the Electron app.)
# Build stamp for the update-available prompt. `next build` loads
# next.config.js more than once, so the stamp must be computed ONCE here
# and handed to the build via env — that guarantees the client bundle and
# the /api/version route inline the SAME id for one image. Overridable
# with --build-arg NEXT_PUBLIC_BUILD_ID=<git-sha> from CI; defaults to
# the epoch second the build started.
ARG NEXT_PUBLIC_BUILD_ID
RUN NEXT_PUBLIC_BUILD_ID="${NEXT_PUBLIC_BUILD_ID:-$(date +%s)}" npm run build

# --- runner --------------------------------------------------
FROM node:20-alpine
WORKDIR /app

# Next.js standalone bundle ships its own minimal node_modules.
COPY --from=builder /app/.next-fresh/standalone ./
COPY --from=builder /app/.next-fresh/static ./.next-fresh/static
COPY --from=builder /app/public ./public

EXPOSE 3002
ENV PORT=3002
ENV HOSTNAME=0.0.0.0
ENV NODE_ENV=production
CMD ["node", "server.js"]
