// Vitest config for the admin-ui unit lane.
//
// - jsdom environment (the api client reads document.cookie for the
//   double-submit CSRF echo).
// - No path alias: admin-ui's tsconfig defines none — imports are
//   relative throughout the app.

import { defineConfig } from "vitest/config";

export default defineConfig({
  // FIX84 — the first .tsx test in this lane: Next's tsconfig keeps jsx
  // "preserve", which esbuild cannot execute; the automatic runtime lets
  // component tests import pages directly.
  // (same trick as apps/web-chat/vitest.config.ts: the oxc transform, no plugin)
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
