// Vitest config for the web-chat unit/component lane.
//
// - jsdom environment so store/api tests can touch window/document.
// - "@" alias mirrors tsconfig.json ("@/*" → "./src/*").
// - oxc jsx runtime:"automatic" overrides tsconfig's jsx:"preserve"
//   (which is Next-bundler-only and would otherwise make Vite's oxc
//   transform leave JSX untranspiled → "invalid JS syntax" on every
//   .tsx test), enabling component tests (Phase 12) without pulling
//   in @vitejs/plugin-react.
// - Playwright specs live under tests/e2e and are explicitly excluded —
//   they run via `npm run test:e2e`, never in this fast lane.

import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  oxc: {
    jsx: {
      runtime: "automatic",
    },
  },
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
