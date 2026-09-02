// FIX44 Part B — the check must catch what it exists to catch.
//
// Proof by construction: hand the real engine + the real config a class we
// KNOW Tailwind cannot build (an opacity modifier on a bare var() colour) and
// the check must name it; hand it the same class without the modifier and it
// must stay quiet. A checker that passes on a planted defect is worse than
// none, because it would let the next #42 through with a green tick.
import { describe, expect, it } from "vitest";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { buildCss, findMissing, selectorOf } = require("./check-generated-classes.js") as {
  buildCss: (config: unknown, raw: string) => Promise<string>;
  findMissing: (wanted: string[], css: string) => string[];
  selectorOf: (cls: string) => string;
};
// eslint-disable-next-line @typescript-eslint/no-require-imports
const config = require("../tailwind.config.js");

describe("FIX44 — generated-classes check", () => {
  it("flags a planted class the engine cannot build, and only that one", async () => {
    // FIX45 Part A: the opaque tokens are channel-form now, so their /NN
    // modifiers ARE generated. The border tokens still carry their own
    // alpha (rgba var()), so a modifier on them stays unbuildable — that
    // is the planted defect this check must keep naming.
    const wanted = ["bg-status-danger/10", "bg-status-danger", "border-border/60", "border-border", "hover:bg-status-danger/10", "focus:ring-border-strong/40", "hover:bg-bg-elevated", "text-text-muted"];
    const css = await buildCss(config, `<div class="${wanted.join(" ")}"></div>`);
    expect(findMissing(wanted, css)).toEqual(["border-border/60", "focus:ring-border-strong/40"]);
    expect(css).toContain("rgb(var(--error-rgb) / 0.1)");
  });

  it("would pass a config that CAN build the modifier (the L1 shape)", async () => {
    const l1 = {
      ...config,
      theme: { extend: { colors: { status: { danger: "rgb(var(--error-rgb) / <alpha-value>)" } } } },
    };
    const css = await buildCss(l1, '<div class="bg-status-danger/10 bg-status-danger"></div>');
    expect(findMissing(["bg-status-danger/10", "bg-status-danger"], css)).toEqual([]);
    expect(css).toContain("rgb(var(--error-rgb) / 0.1)");
  });

  it("escapes selectors the way Tailwind emits them", () => {
    expect(selectorOf("hover:bg-status-danger/10")).toBe(".hover\\:bg-status-danger\\/10");
    expect(selectorOf("border-border")).toBe(".border-border");
  });
});
