/** @type {import('next').NextConfig} */
// Server-side rewrites need a URL reachable by the Next.js Node
// process, not the browser. In docker-compose the backend lives at
// http://backend:8080 (service DNS); bare-metal dev uses
// http://localhost:8080 (the fallback, for `npm run dev` outside Docker).
//
// FIX134 A2 — BACKEND_URL is read when `next build` runs, NOT when the
// server starts: with output 'standalone' the rewrite destinations are
// written into the build (routes manifest). It therefore reaches the
// image as a BUILD ARG (portal/Dockerfile, from compose's
// build.args / PORTAL_BACKEND_URL); the runtime `environment:` twin in
// compose serves only the code that reads process.env.BACKEND_URL per
// request (lib/session-check.ts, the cookie-bridge route). An image built
// without the arg rewrote to localhost:8080 inside the container
// (/api/health 500, /api/portal/me 503, ECONNREFUSED in the portal log).
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8080";

const nextConfig = {
  output: 'standalone',
  async rewrites() {
    return [
      {
        // Exclude /api/auth/* from the backend proxy: NextAuth's
        // /api/auth/[...nextauth] is a DYNAMIC app route, and Next
        // resolves afterFiles rewrites BEFORE dynamic routes — the
        // bare /api/:path* pattern shadowed it and every NextAuth
        // endpoint 404'd off the backend. (/api/oauth/cookie-bridge
        // is a static route, matched in the filesystem phase, so it
        // never needed the exclusion.)
        source: "/api/:path((?!auth).*)",
        destination: `${BACKEND_URL}/:path`,
      },
      {
        source: "/static/:path*",
        destination: `${BACKEND_URL}/static/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
