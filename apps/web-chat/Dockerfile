# --- builder -------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app

# Install deps separately so the layer caches.
COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build

# --- runner --------------------------------------------------
FROM node:20-alpine
WORKDIR /app

# Next.js standalone bundle ships its own minimal node_modules.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3002
ENV PORT=3002
ENV HOSTNAME=0.0.0.0
ENV NODE_ENV=production
CMD ["node", "server.js"]
