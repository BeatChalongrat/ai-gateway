// FIX132 F16 (second half) — a closed slide-over pane stayed in the Tab
// order. Measured on the branch at 360 px (probe/dev.json tab360): after the
// sidebar was made inert, three Tab stops still landed off screen at
// x = 680 — the close buttons of the closed workspace, canvas and preview
// panes (translated off to the right, aria-hidden, but focusable). A closed
// overlay pane is now inert too.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SidePane } from "./SidePane";

function viewport(docked: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q === "(min-width: 1024px)" ? docked : false,
    media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
const Pane = ({ open }: { open: boolean }) => (
  <SidePane open={open} label="Canvas" maxWidth={672} closeLabel="Close canvas" onClose={() => {}} testId="pane">
    <button type="button">inside</button>
  </SidePane>
);
afterEach(cleanup);

describe("FIX132 F16 — a closed overlay pane is inert", () => {
  it("closed (phone / narrow): inert, so its close button is not a Tab stop", () => {
    viewport(false);
    render(<Pane open={false} />);
    expect(screen.getByTestId("pane").hasAttribute("inert")).toBe(true);
  });
  it("open: not inert", () => {
    viewport(false);
    render(<Pane open />);
    expect(screen.getByTestId("pane").hasAttribute("inert")).toBe(false);
  });
});
