// FIX43 Part A — the knob must MOVE, not just the aria state.
//
// The bug this guards: two toggles positioned the knob `absolute` with no
// horizontal inset, so it sat at the track's centre (a <button> is
// text-align:center) — "off" looked nearly on and "on" left the track. Their
// aria-checked tests passed throughout. jsdom lays nothing out, so this test
// pins the geometry the way the CSS expresses it: the knob's x-offset is
// declared (a translate the browser cannot substitute), the two states
// declare DIFFERENT offsets, both keep the 20 px knob inside the 44 px
// track, and nothing in the tree relies on static positioning.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SWITCH_KNOB_OFF_PX, SWITCH_KNOB_ON_PX, Switch } from "./Switch";

const TRACK_PX = 44;
const KNOB_PX = 20;

/** Tailwind translate-x classes → pixels (0.5 → 2px, 5 → 20px, [22px] → 22px). */
function knobOffsetPx(knob: HTMLElement): number {
  const cls = [...knob.classList].find((c) => c.startsWith("translate-x-"));
  if (!cls) throw new Error("knob declares no horizontal offset — it would sit at its static position");
  const v = cls.slice("translate-x-".length);
  const m = v.match(/^\[(\d+)px\]$/);
  if (m) return Number(m[1]);
  return Number(v) * 4; // Tailwind spacing scale: 1 = 0.25rem = 4px
}

afterEach(cleanup);

describe("FIX43 — Switch geometry", () => {
  it("declares a different knob offset for on and off, both inside the track", () => {
    const { rerender } = render(<Switch checked={false} onChange={() => {}} label="x" />);
    const knob = () => screen.getByTestId("switch-knob");
    const off = knobOffsetPx(knob());
    rerender(<Switch checked onChange={() => {}} label="x" />);
    const on = knobOffsetPx(knob());
    expect(off).toBe(SWITCH_KNOB_OFF_PX);
    expect(on).toBe(SWITCH_KNOB_ON_PX);
    expect(on).toBeGreaterThan(off + KNOB_PX / 2); // visibly a different position, not a nudge
    expect(off).toBeGreaterThanOrEqual(0);
    expect(on + KNOB_PX).toBeLessThanOrEqual(TRACK_PX); // never outside the track
  });

  it("does not rely on static position: the track is a flex row and the knob is not absolute", () => {
    render(<Switch checked onChange={() => {}} label="x" />);
    const track = screen.getByRole("switch");
    const knob = screen.getByTestId("switch-knob");
    expect(track.className).toMatch(/\binline-flex\b/);
    expect(knob.className).not.toMatch(/\babsolute\b/);
    expect(track.className).toMatch(/\bw-11\b/);
    expect(knob.className).toMatch(/\bw-5\b/);
  });

  it("flips on click and exposes the state to assistive tech", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Minimize to tray" />);
    const sw = screen.getByRole("switch", { name: "Minimize to tray" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
