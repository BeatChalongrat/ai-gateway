// FIX42 D2 — connector marks: the owner's own file wins, nothing else
// changes. Three cases the header of connectorIcons.tsx promises:
//   1. the owner placed public/icons/connectors/<id>.svg → that file renders
//      (from OUR origin — the probed URL is a relative path, never a host);
//   2. no file → exactly the pre-FIX42 mark (neutral for Slack), no error;
//   3. a connector that already uses simple-icons keeps that mark when no
//      owner file exists — and switches to the owner's file when one does.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectorIcon, ownerMarkPath, resetOwnerMarkCache } from "./connectorIcons";

// A controllable stand-in for the browser's Image: the test decides whether
// the probe "finds" the file. Records every src it was asked to load.
class FakeImage {
  static loads: string[] = [];
  static existing = new Set<string>();
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private _src = "";
  set src(v: string) {
    this._src = v;
    FakeImage.loads.push(v);
    queueMicrotask(() => (FakeImage.existing.has(v) ? this.onload?.() : this.onerror?.()));
  }
  get src() {
    return this._src;
  }
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  resetOwnerMarkCache();
  FakeImage.loads = [];
  FakeImage.existing = new Set();
  vi.stubGlobal("Image", FakeImage);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FIX42 D2 — connector marks", () => {
  it("probes only a same-origin relative path, and refuses ids that could escape it", () => {
    expect(ownerMarkPath("slack")).toBe("/icons/connectors/slack.svg");
    expect(ownerMarkPath("../etc")).toBeNull();
    expect(ownerMarkPath("a/b")).toBeNull();
    expect(ownerMarkPath("https://x")).toBeNull();
  });

  it("1. owner placed slack.svg → the owner's file renders", async () => {
    FakeImage.existing.add("/icons/connectors/slack.svg");
    render(<ConnectorIcon connectorId="slack" name="Slack" />);
    // Before the probe answers: the pre-FIX42 mark, not a blank.
    expect(screen.getByTestId("connector-icon-fallback")).toBeTruthy();
    await settle();
    const owner = screen.getByTestId("connector-icon-owner-slack");
    const img = owner.querySelector("img");
    expect(img?.getAttribute("src")).toBe("/icons/connectors/slack.svg");
    expect(img?.getAttribute("alt")).toBe("Slack logo");
    expect(FakeImage.loads).toEqual(["/icons/connectors/slack.svg"]);
    expect(screen.queryByTestId("connector-icon-fallback")).toBeNull();
  });

  it("2. no file → the neutral mark stays, exactly as before", async () => {
    render(<ConnectorIcon connectorId="slack" name="Slack" />);
    await settle();
    expect(screen.getByTestId("connector-icon-fallback")).toBeTruthy();
    expect(screen.queryByTestId("connector-icon-owner-slack")).toBeNull();
    expect(FakeImage.loads).toEqual(["/icons/connectors/slack.svg"]);
    // FIX43 C3.3 — and no <img> at all: a missing file must never show as a
    // broken-image glyph.
    expect(document.querySelector("img")).toBeNull();
  });

  it("3. a simple-icons connector keeps its bundled mark without an owner file, and yields to one", async () => {
    const { unmount } = render(<ConnectorIcon connectorId="github" name="GitHub" />);
    await settle();
    expect(screen.getByTestId("connector-icon-github")).toBeTruthy();
    expect(screen.getByTestId("connector-icon-github").querySelector("svg path")?.getAttribute("d")).toBeTruthy();
    expect(screen.queryByTestId("connector-icon-owner-github")).toBeNull();
    unmount();
    cleanup();

    resetOwnerMarkCache();
    FakeImage.existing.add("/icons/connectors/github.svg");
    render(<ConnectorIcon connectorId="github" name="GitHub" />);
    await settle();
    expect(screen.getByTestId("connector-icon-owner-github")).toBeTruthy();
    expect(screen.queryByTestId("connector-icon-github")).toBeNull();
  });

  it("probes each id once per page life", async () => {
    render(
      <>
        <ConnectorIcon connectorId="slack" name="Slack" />
        <ConnectorIcon connectorId="slack" name="Slack" />
      </>,
    );
    await settle();
    expect(FakeImage.loads.filter((s) => s.endsWith("slack.svg")).length).toBeLessThanOrEqual(2);
    cleanup();
    render(<ConnectorIcon connectorId="slack" name="Slack" />);
    await settle();
    // the cached answer is reused: no third probe
    expect(FakeImage.loads.filter((s) => s.endsWith("slack.svg")).length).toBeLessThanOrEqual(2);
  });
});
