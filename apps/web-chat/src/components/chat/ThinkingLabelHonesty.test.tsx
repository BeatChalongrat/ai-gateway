// Label honesty: "Thinking…" is reserved for REAL reasoning.
//
// The first-token wait indicator (CatIndicator in MessageBubble) fires
// for every model on every turn — including models that cannot reason —
// so it must not borrow the reasoning vocabulary. The ThinkingBlock, by
// contrast, only mounts on real reasoning_delta / analysis_status
// frames and keeps the word.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { dictionaries } from "@/lib/i18n/dict";

import { CatIndicator } from "./CatIndicator";
import { ThinkingBlock } from "./ThinkingBlock";

describe("waiting indicator label", () => {
  it("renders the neutral waiting label, not the reasoning one", () => {
    render(<CatIndicator labelKey="gen.working" />);
    expect(
      screen.getByText(dictionaries.en["gen.working"]),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(dictionaries.en["reasoning.thinking"]),
    ).toBeNull();
  });

  it("has distinct EN and TH strings from the reasoning label", () => {
    // Guards against someone "simplifying" the two keys back into one.
    expect(dictionaries.en["gen.working"]).not.toBe(
      dictionaries.en["reasoning.thinking"],
    );
    expect(dictionaries.th["gen.working"]).not.toBe(
      dictionaries.th["reasoning.thinking"],
    );
  });
});

describe("ThinkingBlock keeps the reasoning label", () => {
  it("shows reasoning.thinking while real reasoning streams", () => {
    render(<ThinkingBlock content="step one…" thinking />);
    expect(
      screen.getByText(dictionaries.en["reasoning.thinking"]),
    ).toBeInTheDocument();
  });

  it("renders nothing when there is no reasoning at all", () => {
    const { container } = render(
      <ThinkingBlock content="" thinking={false} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
