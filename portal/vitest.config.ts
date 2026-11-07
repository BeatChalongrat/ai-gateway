// Vitest config for the portal unit lane.
//
// - jsdom environment (the api client checks typeof window).
// - "@" alias mirrors tsconfig.json ("@/*" → "./*", i.e. the portal root).

import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // FIX86 — the portal's first .tsx test: tsconfig keeps jsx "preserve",
  // which cannot execute; the automatic runtime lets vitest render
  // components (same setting admin-ui adopted in FIX84).
  oxc: { jsx: { runtime: "automatic" } },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
