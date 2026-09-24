// FIX132 F23 — "การจัดการบทสนทนา" named 47 sidebar row menus AND the chat's
// own menu (NIGHT3 extra.log f23): a screen-reader user hearing the list of
// buttons could not tell which chat a menu belonged to. Each row's menu now
// carries its chat's title.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Conversation } from "@/lib/types";

import { ConversationItem } from "./ConversationItem";

const th = dictionaries.th;
const conv = (id: string, title: string) => ({ id, title, model_id: "m", message_count: 2, created_at: "", updated_at: "" }) as Conversation;
beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 F23 — each row menu is named after its chat", () => {
  it("two rows, two different menu names, both saying whose menu it is", () => {
    const noop = () => {};
    render(
      <>
        <ConversationItem conv={conv("a", "ทริปเกียวโต")} active={false} onSelect={noop} onRename={noop} onDelete={noop} onTogglePin={noop} />
        <ConversationItem conv={conv("b", "งบไตรมาส")} active={false} onSelect={noop} onRename={noop} onDelete={noop} onTogglePin={noop} />
      </>,
    );
    const fill = (s: string, title: string) => s.replace("{title}", title);
    expect(screen.getByRole("button", { name: fill(th["a11y.conversationActionsFor"], "ทริปเกียวโต") })).toBeTruthy();
    expect(screen.getByRole("button", { name: fill(th["a11y.conversationActionsFor"], "งบไตรมาส") })).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: th["a11y.conversationActions"] })).toHaveLength(0);
  });
});
