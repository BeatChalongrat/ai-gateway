// FIX132 N7 — the starter chips' focus ring. NIGHT3 read no ring on
// เรียนรู้ / โค้ด / วิเคราะห์ when reached with Tab. Measured again on the live
// page (scripts/probes/FIX132/results/lanes/lane-ux/probe/n7.json and
// n7-chips-1280.png): the ring faded in over 150 ms (the chip animates
// `all`, the outline too), its top edge was cut off by the chip row's
// overflow box, and the chip turned into a 6-px rounded rectangle while
// focused. Now the outline is not animated, the row leaves room for it, and
// the chip keeps its pill shape. jsdom has no layout: the classes are pinned.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { EmptyState } from "./EmptyState";

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("FIX132 N7 — starter chips show a whole focus ring at once", () => {
  it("no animated outline, pill shape kept, room around the ring", () => {
    render(<EmptyState onPrompt={() => {}} />);
    for (const key of ["empty.chip.write", "empty.chip.learn", "empty.chip.code", "empty.chip.analyze"] as const) {
      const chip = screen.getByRole("button", { name: dictionaries.th[key] });
      const cls = chip.className.split(/\s+/);
      expect(cls).toContain("focus-ring");
      expect(cls).not.toContain("transition-all");
      expect(cls).toContain("focus-visible:!rounded-full");
      const row = chip.parentElement as HTMLElement;
      expect(row.className.split(/\s+/)).toContain("p-1");
    }
  });
});
