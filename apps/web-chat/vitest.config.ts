// Vitest config for the web-chat unit/component lane.
//
// - jsdom environment so store/api tests can touch window/document.
// - "@" alias mirrors tsconfig.json ("@/*" → "./src/*").
// - Vitest 4's oxc transform compiles .tsx with the automatic JSX
//   runtime by default (tsconfig's jsx:"preserve" is bundler-only),
//   so no react plugin is needed for component tests.
// - Playwright specs live under tests/e2e and are explicitly excluded —
//   they run via `npm run test:e2e`, never in this fast lane.

import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", "tests/e2e/**", ".next/**"],
  },
});
