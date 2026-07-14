// Vitest config for the admin-ui unit lane.
//
// - jsdom environment (the api client reads document.cookie for the
//   double-submit CSRF echo).
// - No path alias: admin-ui's tsconfig defines none — imports are
//   relative throughout the app.

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
