// FIX132 F32 — a long code line scrolled with no hint. NIGHT3 j12-02 at
// 360 px: the highlighted <code> (highlight.js makes it its own scroller)
// was 1,648 px wide in a 294-px box, with no visible scrollbar, fade or
// arrow — nothing said the line went on. Now the block body is the one
// scroller (thin visible scrollbar), and a fade on the right edge shows
// while there is more to the right; it goes when scrolled to the end.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/canvas/CanvasSourceContext", () => ({ useCanvasSource: () => null }));

import { CodeBlock } from "./CodeBlock";

afterEach(cleanup);

function geometry(el: HTMLElement, { scrollWidth, clientWidth }: { scrollWidth: number; clientWidth: number }) {
  Object.defineProperty(el, "scrollWidth", { configurable: true, get: () => scrollWidth });
  Object.defineProperty(el, "clientWidth", { configurable: true, get: () => clientWidth });
}

describe("FIX132 F32 — a line wider than the block says so", () => {
  it("a fade on the right while there is more; none once scrolled to the end", () => {
    const long = `x = "${"ก".repeat(400)}"`;
    const { container } = render(<CodeBlock><code className="language-python hljs">{long}</code></CodeBlock>);
    const body = container.querySelector(".code-block-body") as HTMLElement;
    geometry(body, { scrollWidth: 1648, clientWidth: 294 });
    act(() => void fireEvent.scroll(body));
    expect(screen.getByTestId("code-scroll-hint")).toBeTruthy();
    body.scrollLeft = 1648 - 294;
    act(() => void fireEvent.scroll(body));
    expect(screen.queryByTestId("code-scroll-hint")).toBeNull();
  });

  it("a block that fits shows no hint", () => {
    const { container } = render(<CodeBlock><code className="language-python">{"print(1)"}</code></CodeBlock>);
    const body = container.querySelector(".code-block-body") as HTMLElement;
    geometry(body, { scrollWidth: 294, clientWidth: 294 });
    act(() => void fireEvent.scroll(body));
    expect(screen.queryByTestId("code-scroll-hint")).toBeNull();
  });
});
