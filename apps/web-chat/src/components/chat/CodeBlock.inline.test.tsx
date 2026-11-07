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

  // FIX81 Part B — the hard requirement: the user must never see a
  // rendered-but-non-functional copy of their own artifact. An html block
  // that needs script gets NO live frame; it gets a card that opens the
  // canvas (where allow-scripts is granted deliberately), and the raw
  // source keeps rendering below (fold, never remove).
  it("script-dependent html renders a CARD, never a script-less live frame", () => {
    const game = `<!doctype html><html><body><button onclick="play()">เริ่มเกม</button><script>function play(){}</script></body></html>`;
    render(<CodeBlock><code className="language-html">{game}</code></CodeBlock>);
    expect(screen.queryByTestId("inline-artifact")).toBeNull();
    expect(document.querySelector("iframe")).toBeNull();
    expect(screen.getByTestId("interactive-artifact-card")).toBeTruthy();
    // the raw source still shows beneath the card
    expect(screen.getByText(/function play/)).toBeTruthy();
  });

  it("an on*-handler alone (no <script> tag) is enough to force the card", () => {
    render(<CodeBlock><code className="language-html">{`<div onclick="go()">x</div>`}</code></CodeBlock>);
    expect(screen.queryByTestId("inline-artifact")).toBeNull();
    expect(screen.getByTestId("interactive-artifact-card")).toBeTruthy();
  });

  it("svg keeps its inline frame (script is stripped by design, so the render is faithful)", () => {
    render(<CodeBlock><code className="language-svg">{"<svg><circle r='5'/></svg>"}</code></CodeBlock>);
    const frame = screen.getByTestId("inline-artifact").querySelector("iframe")!;
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(screen.queryByTestId("interactive-artifact-card")).toBeNull();
  });

  // FIX82 ② — ONE Open-in-Canvas affordance per block (the FIX77 rule,
  // different widget): the card carries the action for script-needing
  // html, so the header button hides there and stays everywhere else.
  it("exactly one Open affordance renders per block, and Copy is untouched", () => {
    const openLabel = "Open in Canvas";
    // card case: script-needing html → card only, no header button
    const { unmount } = render(<CodeBlock><code className="language-html">{`<script>go()</script>`}</code></CodeBlock>);
    expect(screen.getByTestId("interactive-artifact-card")).toBeTruthy();
    expect(screen.getAllByText(openLabel)).toHaveLength(1);
    expect(screen.getByLabelText("Copy code")).toBeTruthy();
    unmount();
    // header case: script-free html → inline frame + header button only
    render(<CodeBlock><code className="language-html">{"<h1>static</h1>"}</code></CodeBlock>);
    expect(screen.queryByTestId("interactive-artifact-card")).toBeNull();
    expect(screen.getAllByText(openLabel)).toHaveLength(1);
    cleanup();
    // no-frame types (markdown / mermaid / react): header button only
    for (const [langCls, src] of [["language-markdown", "# hi"], ["language-mermaid", "graph TD; A-->B"], ["language-jsx", "export default () => <b>x</b>"]] as const) {
      render(<CodeBlock><code className={langCls}>{src}</code></CodeBlock>);
      expect(screen.getAllByText(openLabel)).toHaveLength(1);
      expect(screen.queryByTestId("interactive-artifact-card")).toBeNull();
      cleanup();
    }
  });
});
