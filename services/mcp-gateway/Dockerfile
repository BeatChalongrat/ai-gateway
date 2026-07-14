FROM node:20-alpine
WORKDIR /app
COPY package.json ./
# No lockfile committed — resolve at build time (pin majors in package.json).
RUN npm install --omit=dev
COPY server.js lib.js ./
RUN mkdir -p /data/workspace
# 3300-3379 are the per-user child ports (built-in AND oauth2 — Phase 10
# C1) — internal only (the JWT-verified proxy route on :3200 fronts them);
# not EXPOSEd on purpose. The legacy shared :3201/:3202 children are gone.
EXPOSE 3200
CMD ["node", "server.js"]
