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
RUN npm run build

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
