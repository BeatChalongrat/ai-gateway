// Batch 2 T4 — hardcoded-string regression pins.
//
// The key-parity sweep proves both dictionaries carry the same KEYS;
// it cannot see a component that bypasses the dict entirely. This
// test source-scans the surfaces localized in the T4 sweep and fails
// if any of their previously-hardcoded English literals reappear —
// the cheap, targeted enforcement until the full-repo sweep (the
// remaining ~200 inventory rows) ships as its own batch.

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..", "..");

const PINS: Array<{ file: string; forbidden: string[] }> = [
  {
    file: "components/chat/ShareModal.tsx",
    forbidden: [
      '"Share conversation"',
      "Anyone with the link can view a read-only copy.",
      "Share URL",
      "Never expires",
      "Revoke this link?",
      "Link expires after",
      '"Create link"',
    ],
  },
  {
    file: "components/chat/ModelSelector.tsx",
    forbidden: [
      '"Video Generation"',
      '"Audio / Music"',
      '"Text-to-Speech"',
      "No models available",
      "No models found",
      'free: "Free"',
      "Cost tier: $",
    ],
  },
  {
    file: "components/chat/CreditBadge.tsx",
    forbidden: [
      ">Usage<",
      "View in Portal",
      "credits remaining",
      "Usage by model",
      "No usage yet.",
      "Total used",
    ],
  },
  {
    file: "components/chat/ConversationItem.tsx",
    forbidden: [
      '"Untitled"',
      "Delete this conversation?",
      "temporary chat (memory off)",
    ],
  },
  {
    file: "components/chat/MemoryIndicator.tsx",
    forbidden: [
      '"Memory is active"',
      '"Memory is off for this chat"',
      "Nothing said here will be saved",
      "Open Memory Settings",
      "Turn off for this chat",
    ],
  },
  // ── FIX33 B7 — the five desktop-only pages. settings + writing-style are
  // fully localised; connectors / skills / tasks had their toasts, errors and
  // attribute strings moved. FIX34.1 Part F finished B7: skills + tasks JSX
  // prose is in the dict too, and the "available in the SRAN Desktop app"
  // wall is gone everywhere (desktop-only routes redirect on the web — the
  // `desktopOnly.notice` key no longer exists, so any use is a compile error).
  {
    file: "app/chat/settings/page.tsx",
    forbidden: [
      "Couldn't load settings.",
      ">Settings<",
      '"Minimize to tray"',
      '"Approved sites"',
      '"Extension folder to load"',
      "This screen is available in the SRAN Desktop app.",
      "desktopOnly.notice",
    ],
  },
  // FIX43 Part B — the two halves of /chat/settings and the sidebar's
  // account menu (its five labels used to be bare English).
  {
    file: "app/chat/settings/DeviceSettings.tsx",
    forbidden: ["Couldn't load settings.", '"Minimize to tray"', '"Approved sites"', '"Extension folder to load"'],
  },
  {
    file: "app/chat/settings/WebSettings.tsx",
    forbidden: ['"Signed in as"', '"Appearance"', '"Account"', '"Language"', '">Open<"', '"Sign out'],
  },
  {
    file: "components/chat/Sidebar.tsx",
    forbidden: ['label="Settings"', 'label="Scheduled tasks"', 'label="Connectors"', 'label="Writing style"', 'label="Memory"'],
  },
  {
    file: "app/chat/writing-style/page.tsx",
    forbidden: [
      "Couldn't load your writing style.",
      "Learn my style",
      '"Couldn\'t save."',
      "Paste your writing samples here",
      "This screen is available in the SRAN Desktop app.",
      "desktopOnly.notice",
    ],
  },
  {
    file: "app/quick/page.tsx",
    forbidden: ["desktopOnly.notice", "SRAN Desktop app"],
  },
  {
    file: "app/chat/connectors/page.tsx",
    forbidden: [
      "Couldn't load the connector catalog.",
      'placeholder="Search connectors',
      '"No connectors are available yet."',
      "This screen is available in the SRAN Desktop app.",
      "approvals are handled by the desktop app",
      ">Connectors<",
      "Add custom connector",
    ],
  },
  {
    file: "app/chat/skills/page.tsx",
    forbidden: [
      "Couldn't load skills.",
      'title="Delete skill"',
      'aria-label="Create skill"',
      // FIX34.1 Part F — JSX prose
      "Instruction sets the assistant follows",
      "Capabilities the assistant can invoke",
      "Write the instructions the assistant must follow",
      "No skills installed yet",
      "Install a skill file",
      "Install package",
      "Applies to every message",
      "Trigger words, comma-separated",
      '"Built-in"',
      "This can't be undone.",
      "Back to chat",
    ],
  },
  {
    file: "app/chat/tasks/page.tsx",
    forbidden: [
      "Couldn't load scheduled tasks.",
      'placeholder="Morning briefing"',
      '"Task run failed."',
      // FIX34.1 Part F — the web dead end + JSX prose
      "not in the browser",
      "Available in the SRAN Desktop app",
      "Runs only while the SRAN Desktop app is open",
      "No on-device tasks",
      "Prompts that run on a schedule inside the desktop app",
      "Standard 5-field cron",
      "Needed your permission for",
      "Ask before each action",
      "Run now",
      "Back to chat",
      "Scheduled tasks",
    ],
  },
  {
    file: "app/chat/tasks/CloudTasksSection.tsx",
    forbidden: [
      "Couldn't load cloud tasks.",
      'placeholder="Daily digest"',
      '"Name and prompt are both required."',
      // FIX34.1 Part F — kind badge + section header + empty state
      "Runs only while the SRAN Desktop app is open",
      '"On this device"',
      "No cloud tasks",
      "Cloud tasks cannot use local files",
    ],
  },
];

describe("T4 hardcoded-string pins", () => {
  for (const { file, forbidden } of PINS) {
    it(`${file} carries no re-hardcoded English`, () => {
      // Comments are fine (doc headers legitimately quote UI copy);
      // only CODE lines must stay literal-free.
      const src = fs
        .readFileSync(path.join(SRC, file), "utf8")
        .split("\n")
        .filter((line) => {
          const t = line.trimStart();
          return !t.startsWith("//") && !t.startsWith("{/*") && !t.startsWith("*");
        })
        .join("\n");
      for (const literal of forbidden) {
        expect(src, `"${literal}" must come from the dict, not the source`).not.toContain(
          literal,
        );
      }
    });
  }
});
