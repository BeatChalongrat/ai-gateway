const { PHASE_DEVELOPMENT_SERVER } = require("next/constants");

// Build stamp for the update-available prompt: baked into BOTH the client
// bundle (NEXT_PUBLIC_) and the server process at build time. A long-open
// tab compares its baked value against GET /api/version (served by the
// NEW deploy) — mismatch → "refresh to update" banner.
//
// MUST come from the environment in real builds (the Dockerfile stamps
// NEXT_PUBLIC_BUILD_ID once before `next build`): `next build` loads this
// config more than once (separate server/client compilation passes), so a
// Date.now() evaluated here bakes DIFFERENT ids into the client bundle vs
// the /api/version route of the SAME image — a permanent false-positive
// banner. Date.now() remains only as a dev-server fallback, where the
// config is loaded once per `next dev` start.
const BUILD_ID = process.env.NEXT_PUBLIC_BUILD_ID || String(Date.now());

/** @type {(phase: string) => import('next').NextConfig} */
module.exports = (phase) => ({
  output: "standalone",
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BUILD_ID: BUILD_ID,
  },
  // Keep dev and production build output separate. Sharing .next-fresh
  // between `next dev` and `next build` leaves the dev server requiring
  // chunks that a build just replaced or removed.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next-fresh",
  // The chat-edge-api lives on a separate origin (port 4000); we send
  // CORS-allowed requests directly from the browser instead of proxying.
  // env.NEXT_PUBLIC_API_URL drives the API base in lib/api.ts.
});
