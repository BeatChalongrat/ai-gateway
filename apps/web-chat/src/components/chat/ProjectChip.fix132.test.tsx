// FIX132 F30 — nothing said you were inside a project. NIGHT3 p-12/p-13:
// "แชทใหม่ในโปรเจกต์นี้" landed on the generic welcome screen, and after the
// first send the sidebar nested the chat under the project but the main pane
// never said so. Now a project chip names the project in the chat header,
// and the new-chat screen says the chat will go into it (with a way out).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Project } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { ProjectChip } from "./ProjectChip";

const th = dictionaries.th;
const fill = (s: string, v: Record<string, string>) => s.replace(/\{(\w+)\}/g, (_, k) => v[k] ?? `{${k}}`);
const initial = useChatStore.getState();

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial, projects: [{ id: "p1", name: "ทริปญี่ปุ่น", instructions: "", conversation_count: 1, created_at: "", updated_at: "" } as Project] }, true);
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 F30 — the project you are in is named", () => {
  it("in a chat: 'โปรเจกต์: <name>', a link to the project", () => {
    render(<ProjectChip projectId="p1" />);
    const link = screen.getByRole("link", { name: fill(th["project.chip.in"], { name: "ทริปญี่ปุ่น" }) });
    expect(link.getAttribute("href")).toBe("/chat/projects?project=p1");
  });

  it("on the new-chat screen: says the new chat goes into the project, and can be taken out", () => {
    const onClear = vi.fn();
    render(<ProjectChip projectId="p1" variant="newChat" onClear={onClear} />);
    expect(screen.getByTestId("project-chip")).toHaveTextContent(fill(th["project.chip.newChat"], { name: "ทริปญี่ปุ่น" }));
    fireEvent.click(screen.getByRole("button", { name: th["project.chip.leave"] }));
    expect(onClear).toHaveBeenCalled();
  });

  it("no project, or an unknown one: nothing", () => {
    const { container } = render(<ProjectChip projectId={null} />);
    expect(container).toBeEmptyDOMElement();
    cleanup();
    const other = render(<ProjectChip projectId="gone" />);
    expect(other.container).toBeEmptyDOMElement();
  });
});
