// FIX128 D4 — keyboard-only navigation of the chat list.
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { moveBetweenChats } from "./Sidebar";

afterEach(cleanup);

function List() {
  return (
    <nav onKeyDown={moveBetweenChats} data-testid="list">
      {["a", "b", "c"].map((id) => (
        <div key={id} role="button" tabIndex={0} data-conversation-id={id}>
          chat {id}
          <input aria-label={`rename ${id}`} />
        </div>
      ))}
    </nav>
  );
}

const row = (c: HTMLElement, id: string) => c.querySelector<HTMLElement>(`[data-conversation-id="${id}"]`)!;

describe("FIX128 D4 — arrow keys move between chats", () => {
  it("ArrowDown / ArrowUp / Home / End move focus between rows", () => {
    const { container } = render(<List />);
    row(container, "a").focus();
    fireEvent.keyDown(row(container, "a"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(row(container, "b"));
    fireEvent.keyDown(row(container, "b"), { key: "End" });
    expect(document.activeElement).toBe(row(container, "c"));
    fireEvent.keyDown(row(container, "c"), { key: "ArrowDown" }); // stays on the last
    expect(document.activeElement).toBe(row(container, "c"));
    fireEvent.keyDown(row(container, "c"), { key: "Home" });
    expect(document.activeElement).toBe(row(container, "a"));
    fireEvent.keyDown(row(container, "a"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(row(container, "a"));
  });

  it("keys typed in a rename box are left alone", () => {
    const { getByLabelText, container } = render(<List />);
    const input = getByLabelText("rename b");
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(document.activeElement).toBe(input);
    expect(document.activeElement).not.toBe(row(container, "c"));
  });
});
