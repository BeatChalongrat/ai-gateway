// Vitest config for the desktop shell's pinning tests (E1 §4).
//
// Node environment, no jsdom: the modules under test are the pure policy
// modules (navigation-policy, ipc-guard, web-preferences, bridge-surface,
// config) — none of them import 'electron', by design, so they can be
// asserted without launching a renderer. Anything that needs a live
// BrowserWindow is out of this lane's reach and says so in its comment.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
});
