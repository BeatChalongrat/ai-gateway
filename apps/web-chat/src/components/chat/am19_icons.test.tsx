// AM19 — one icon per meaning.
//
// The owner found the same glyph on the sidebar's "Temporary chat" door and on
// the button beside the model picker. They are two meanings: the composer's
// button is the thinking toggle (BrainCircuit), the door starts a chat that
// neither uses nor saves memory. So: thinking keeps BrainCircuit and nothing
// else wears it; a temporary chat is the dashed bubble (MessageSquareDashed)
// wherever it is said — door, header chip, banner, row marker, memory page;
// memory switched off on an ordinary chat is the Brain struck through
// (BrainOff); the gear in the memory popover is the app's gear (Settings), not
// a second drawing of one; "new chat" is one bubble-with-plus on every surface.
import fs from "node:fs";
import path from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: false }));
const { mockApi } = vi.hoisted(() => ({
  mockApi: { listConversations: vi.fn(), listProjects: vi.fn(), searchConversations: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => state.hosted }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/chat" }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "somchai@acme.ai.gateway" } }) }));
vi.mock("@/hooks/useKeyboardShortcuts", () => ({ useNewChatHandler: () => vi.fn() }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({ useMemoryPendingStore: () => 0 }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { MemoryIndicator } from "./MemoryIndicator";
import { Sidebar } from "./Sidebar";
import { TemporaryChatBanner } from "./TemporaryChatBanner";

const en = dictionaries.en;
const initialState = useChatStore.getState();
const conv = (over: Partial<Conversation>): Conversation =>
  ({ id: "c1", title: "t", model_id: "m", message_count: 0, created_at: "2026-10-13T00:00:00Z", updated_at: "2026-10-13T00:00:00Z", ...over }) as Conversation;

/** The lucide glyph names drawn inside an element (lucide puts `lucide-<name>` on every svg it makes). */
function glyphs(el: ParentNode): string[] {
  return [...el.querySelectorAll("svg")].flatMap((svg) =>
    (svg.getAttribute("class") ?? "").split(/\s+/).filter((c) => c.startsWith("lucide-") && !c.endsWith("-icon")),
  );
}

beforeEach(() => {
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  state.hosted = false;
  useChatStore.setState({ ...initialState, conversationsLoaded: true }, true);
  useI18nStore.setState({ locale: "en" });
  localStorage.clear();
  mockApi.listConversations.mockResolvedValue({ conversations: [], has_more: false, next_cursor: null });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
});
afterEach(cleanup);

describe("the sidebar's doors", () => {
  it("the temporary-chat door is the dashed bubble, not the composer's thinking glyph", () => {
    render(<Sidebar activeId={null} open collapsed={false} onClose={() => {}} onToggleCollapsed={() => {}} />);
    const door = screen.getByRole("button", { name: new RegExp(en["sidebar.newTemporaryChat"]) });
    expect(glyphs(door)).toEqual(["lucide-message-square-dashed"]);
    expect(glyphs(door)).not.toContain("lucide-brain-circuit");
  });

  it("new chat is the bubble-with-plus", () => {
    render(<Sidebar activeId={null} open collapsed={false} onClose={() => {}} onToggleCollapsed={() => {}} />);
    const door = screen.getByRole("button", { name: new RegExp(`^${en["sidebar.newChat"]}`) });
    expect(glyphs(door)).toEqual(["lucide-message-square-plus"]);
  });
});

describe("the header chip and the banner say which no-memory state it is", () => {
  it("on: Brain · off: BrainOff · temporary: the dashed bubble", () => {
    useChatStore.setState({ conversations: [conv({ memory_enabled: true })] });
    const on = render(<MemoryIndicator conversationId="c1" />);
    expect(glyphs(on.container)).toEqual(["lucide-brain"]);
    cleanup();

    useChatStore.setState({ conversations: [conv({ memory_enabled: false })] });
    const off = render(<MemoryIndicator conversationId="c1" />);
    expect(glyphs(off.container)).toEqual(["lucide-brain-off"]);
    cleanup();

    useChatStore.setState({ conversations: [conv({ memory_enabled: false, is_temporary: true })] });
    const temp = render(<MemoryIndicator conversationId="c1" />);
    expect(glyphs(temp.container)).toEqual(["lucide-message-square-dashed"]);
  });

  it("the popover's settings row wears the app's gear, and 'turn off' the struck-through brain", () => {
    useChatStore.setState({ conversations: [conv({ memory_enabled: true })] });
    render(<MemoryIndicator conversationId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en["memory.indicator.on"]) }));
    expect(glyphs(screen.getByRole("menuitem", { name: en["memory.openSettings"] }))).toEqual(["lucide-settings"]);
    expect(glyphs(screen.getByRole("menuitem", { name: en["memory.popover.toggleOff"] }))).toEqual(["lucide-brain-off"]);
  });

  it("the banner: temporary is the dashed bubble, off is the struck-through brain", () => {
    useChatStore.setState({ conversations: [conv({ memory_enabled: false, is_temporary: true })] });
    const temp = render(<TemporaryChatBanner conversationId="c1" />);
    expect(glyphs(temp.container)).toEqual(["lucide-message-square-dashed"]);
    cleanup();
    useChatStore.setState({ conversations: [conv({ memory_enabled: false })] });
    const off = render(<TemporaryChatBanner conversationId="c1" />);
    expect(glyphs(off.container)).toEqual(["lucide-brain-off"]);
  });
});

describe("BrainCircuit means thinking, and nothing else draws it", () => {
  it("only the composer's reasoning toggle and the thinking block render it", () => {
    const root = path.resolve(__dirname, "..", "..");
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const name of fs.readdirSync(dir)) {
        const p = path.join(dir, name);
        if (fs.statSync(p).isDirectory()) walk(p);
        else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && /<BrainCircuit\b/.test(fs.readFileSync(p, "utf8"))) {
          hits.push(path.relative(root, p).replace(/\\/g, "/"));
        }
      }
    };
    walk(root);
    expect(hits.sort()).toEqual(["components/chat/ChatInput.tsx", "components/chat/ThinkingBlock.tsx"]);
  });
});
