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
        source: "/api/:path*",
        destination: `${BACKEND_URL}/:path*`,
      },
      {
        source: "/static/:path*",
        destination: `${BACKEND_URL}/static/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
