const { PHASE_DEVELOPMENT_SERVER } = require("next/constants");

/** @type {(phase: string) => import('next').NextConfig} */
module.exports = (phase) => ({
  output: "standalone",
  reactStrictMode: true,
  // Keep dev and production build output separate. Sharing .next-fresh
  // between `next dev` and `next build` leaves the dev server requiring
  // chunks that a build just replaced or removed.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next-fresh",
  // The chat-edge-api lives on a separate origin (port 4000); we send
  // CORS-allowed requests directly from the browser instead of proxying.
  // env.NEXT_PUBLIC_API_URL drives the API base in lib/api.ts.
});
