// A2 — ToolActivityIndicator rendering + live-region tool announcement.

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

vi.mock("./MessageBubble", () => ({
  MessageBubble: () => <div data-testid="bubble" />,
}));

import { ChatThread } from "./ChatThread";
import {
  ToolActivityIndicator,
  toolActivityLabelKey,
} from "./ToolActivityIndicator";

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

describe("ToolActivityIndicator", () => {
  it("shows shimmer label for running and ✓ done label for finished", () => {
    const { getByText, queryByText } = render(
      <ToolActivityIndicator
        activities={[
          { id: "1", tool: "save_memory", phase: "started" },
          { id: "2", tool: "list_memories", phase: "finished" },
        ]}
      />,
    );
    expect(getByText(dictionaries.en["tool.save_memory"])).toBeTruthy();
    // Finished shows the DONE form, not the running label.
    expect(getByText(dictionaries.en["tool.done.list_memories"])).toBeTruthy();
    expect(queryByText(dictionaries.en["tool.list_memories"])).toBeNull();
  });

  it("falls back to the generic labels for unknown (MCP-shaped) tools", () => {
    const { getByText, rerender } = render(
      <ToolActivityIndicator
        activities={[{ id: "1", tool: "jira_search", phase: "started" }]}
      />,
    );
    expect(getByText("Using jira_search…")).toBeTruthy();
    rerender(
      <ToolActivityIndicator
        activities={[{ id: "1", tool: "jira_search", phase: "finished" }]}
      />,
    );
    expect(getByText("Used jira_search")).toBeTruthy();
  });

  it("a fast tool (finished-only snapshot) still shows the ✓ done row", () => {
    // started/finished frames of a ~ms tool can land in one paint —
    // the finished row must persist as the visible evidence.
    const { getByText, container } = render(
      <ToolActivityIndicator
        activities={[{ id: "1", tool: "save_memory", phase: "finished" }]}
      />,
    );
    expect(getByText(dictionaries.en["tool.done.save_memory"])).toBeTruthy();
    expect(container.firstChild).not.toBeNull();
  });

  it("renders nothing only when the list is empty", () => {
    const { container } = render(<ToolActivityIndicator activities={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("label helpers resolve known tools and reject unknown", () => {
    expect(toolActivityLabelKey("save_memory")).toBe("tool.save_memory");
    expect(toolActivityLabelKey("no_such_tool")).toBeNull();
  });
});

describe("ChatThread live region announces the running tool", () => {
  function msg(over: Partial<Message>): Message {
    return {
      id: "m1",
      role: "assistant",
      content: "",
      status: "streaming",
      created_at: "2026-01-01T00:00:00Z",
      ...over,
    };
  }

  it("prefers the running tool's label over Thinking", () => {
    const { container } = render(
      <ChatThread
        convId="c1"
        messages={[
          msg({
            isStreaming: true,
            tool_activities: [
              { id: "t1", tool: "search_past_conversations", phase: "started" },
            ],
          }),
        ]}
        onRegenerate={() => {}}
      />,
    );
    const region = container.querySelector('[aria-live="polite"]');
    expect(region?.textContent).toBe(
      dictionaries.en["tool.search_past_conversations"],
    );
  });
});
