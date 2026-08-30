// FIX29 §2.3 (defect #23) — an html / svg artifact renders in the thread, static sandbox only.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

vi.mock("@/components/canvas/CanvasSourceContext", () => ({ useCanvasSource: () => ({ conversationId: "c1", messageId: "m1" }) }));

import { CodeBlock } from "./CodeBlock";

describe("inline artifact preview", () => {
  it("renders an html block as a sandboxed iframe (no scripts), and keeps the code below it", () => {
    render(<CodeBlock><code className="language-html">{"<h1>hello</h1>"}</code></CodeBlock>);
    const frame = screen.getByTestId("inline-artifact").querySelector("iframe")!;
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toContain("<h1>hello</h1>");
    expect(screen.getByText("<h1>hello</h1>")).toBeTruthy();
  });

  it("a python block gets no preview", () => {
    render(<CodeBlock><code className="language-python">{"print(1)"}</code></CodeBlock>);
    expect(screen.queryByTestId("inline-artifact")).toBeNull();
  });
});
