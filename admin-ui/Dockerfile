FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
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
