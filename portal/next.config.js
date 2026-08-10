/** @type {import('next').NextConfig} */
// Server-side rewrites need a URL reachable by the Next.js Node
// process, not the browser. In docker-compose the backend lives at
// http://backend:8080 (service DNS); bare-metal dev uses
// http://localhost:8080. BACKEND_URL is injected by docker-compose
// and falls back to localhost for `npm run dev` outside Docker.
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
