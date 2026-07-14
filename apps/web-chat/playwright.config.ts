// Playwright config — the E2E lane (npm run test:e2e). Deliberately
// separate from the fast vitest unit lane (`npm test`), which excludes
// tests/e2e. Every backend call is mocked via route interception in the
// specs, so this needs NO gateway / chat-edge / database — just the
// Next.js app itself.

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  // Dev-server compiles routes on first hit — be generous.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: "http://localhost:3102",
    trace: "retain-on-failure",
  },
  webServer: {
    // Dedicated port 3102: the Docker web-chat container usually owns
    // :3002 with a STALE prod bundle (no hot reload — see the repo's
    // "web-chat stale image" gotcha). Reusing it would test old code,
    // so the e2e lane always boots a fresh dev server from source.
    command: "npx next dev -p 3102",
    url: "http://localhost:3102",
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
