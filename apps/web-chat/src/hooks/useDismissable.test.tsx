// FIX84 row 3 — one dismiss rule for every popover: outside pointer-down
// AND Escape close it; Escape is marked handled so the global Esc→stop
// binding does not also fire; nothing listens while closed.

import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { useDismissable } from "./useDismissable";

function Popover({ initiallyOpen = true }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const ref = useRef<HTMLDivElement>(null);
  useDismissable(ref, open, () => setOpen(false));
  return (
    <div>
      <button type="button" onClick={() => setOpen((v) => !v)}>trigger</button>
      <span data-testid="outside">outside</span>
      {open ? (
        <div ref={ref} role="menu" data-testid="pop">
          <button type="button" role="menuitem">inside</button>
        </div>
      ) : null}
    </div>
  );
}

afterEach(cleanup);

describe("useDismissable (FIX84 row 3)", () => {
  it("Escape closes the popover and marks the key event handled", () => {
    const r = render(<Popover />);
    expect(r.queryByTestId("pop")).not.toBeNull();
    const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    act(() => {
      document.dispatchEvent(ev);
    });
    expect(ev.defaultPrevented).toBe(true);
    expect(r.queryByTestId("pop")).toBeNull();
  });

  it("a pointer-down outside closes it; inside does not", () => {
    const r = render(<Popover />);
    fireEvent.mouseDown(r.getByRole("menuitem"));
    expect(r.queryByTestId("pop")).not.toBeNull();
    fireEvent.mouseDown(r.getByTestId("outside"));
    expect(r.queryByTestId("pop")).toBeNull();
  });

  it("an Escape someone else already handled is left alone (dialogs on top keep the key)", () => {
    const r = render(<Popover />);
    const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    ev.preventDefault();
    act(() => {
      document.dispatchEvent(ev);
    });
    expect(r.queryByTestId("pop")).not.toBeNull();
  });

  it("while closed, Escape and outside clicks are not observed", () => {
    const r = render(<Popover initiallyOpen={false} />);
    // would throw / re-render if a stale listener called setOpen; assert no popover appears and no error
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    });
    fireEvent.mouseDown(r.getByTestId("outside"));
    expect(r.queryByTestId("pop")).toBeNull();
    fireEvent.click(r.getByText("trigger"));
    expect(r.queryByTestId("pop")).not.toBeNull();
  });
});
