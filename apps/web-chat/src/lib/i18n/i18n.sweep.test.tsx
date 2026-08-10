// Phase-2 i18n sweep tests.
//
// Three jobs:
//  1. Runtime parity: every EN key exists in TH (and vice versa) with
//     non-empty text — the compile-time Record<DictKey, string> already
//     enforces this, but the runtime check also catches empty strings.
//  2. Locale gating for non-hook consumers: localizeErrorCode /
//     fallbackNotice / tOutside follow the ACTIVE locale (the old
//     errorMessages.ts was hardcoded Thai regardless of locale).
//  3. Component rendering: a swept component (SearchingIndicator)
//     renders the dictionary string for each locale — no stray
//     hardcoded language.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ImageGeneratingPlaceholder } from "@/components/chat/ImageGeneratingPlaceholder";
import { SearchingIndicator } from "@/components/chat/SearchingIndicator";
import { fallbackNotice, localizeErrorCode } from "@/lib/errorMessages";
import { tOutside, useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

afterEach(cleanup);

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

describe("dictionary parity", () => {
  it("EN and TH have identical key sets with non-empty values", () => {
    const enKeys = Object.keys(dictionaries.en).sort();
    const thKeys = Object.keys(dictionaries.th).sort();
    expect(thKeys).toEqual(enKeys);
    for (const k of enKeys) {
      expect(
        dictionaries.en[k as keyof typeof dictionaries.en].length,
        `en["${k}"] empty`,
      ).toBeGreaterThan(0);
      expect(
        dictionaries.th[k as keyof typeof dictionaries.th].length,
        `th["${k}"] empty`,
      ).toBeGreaterThan(0);
    }
  });

  it("swept keys are actually translated (EN ≠ TH) for a sample", () => {
    const sample = [
      "share.toast.created",
      "error.provider_error",
      "gen.image",
      "thread.newMessages",
      "boundary.title",
      "kb.toast.deleted",
    ] as const;
    for (const k of sample) {
      expect(dictionaries.en[k]).not.toBe(dictionaries.th[k]);
    }
  });
});

describe("locale gating (non-hook path)", () => {
  it("localizeErrorCode follows the active locale", () => {
    useI18nStore.setState({ locale: "en" });
    expect(localizeErrorCode("provider_error")).toBe(
      dictionaries.en["error.provider_error"],
    );
    useI18nStore.setState({ locale: "th" });
    expect(localizeErrorCode("provider_error")).toBe(
      dictionaries.th["error.provider_error"],
    );
  });

  it("unknown code falls back to server message, then localized generic", () => {
    useI18nStore.setState({ locale: "th" });
    expect(localizeErrorCode("nope", "server says hi")).toBe("server says hi");
    expect(localizeErrorCode("nope")).toBe(dictionaries.th["error.generic"]);
    useI18nStore.setState({ locale: "en" });
    expect(localizeErrorCode("nope")).toBe(dictionaries.en["error.generic"]);
  });

  it("fallbackNotice substitutes models in the active locale", () => {
    useI18nStore.setState({ locale: "en" });
    expect(fallbackNotice("claude-sonnet-4", "GPT-4o")).toBe(
      "Answered by GPT-4o (claude-sonnet-4 temporarily unavailable)",
    );
    useI18nStore.setState({ locale: "th" });
    expect(fallbackNotice("claude-sonnet-4", "GPT-4o")).toContain("ตอบโดย GPT-4o");
  });

  it("tOutside interpolates {vars}", () => {
    useI18nStore.setState({ locale: "en" });
    expect(tOutside("chat.toast.modelSwitched", { label: "GPT-4o" })).toBe(
      "Switched to GPT-4o",
    );
  });
});

describe("swept components respect the locale", () => {
  it("SearchingIndicator renders EN in en and TH in th", () => {
    useI18nStore.setState({ locale: "en" });
    render(<SearchingIndicator />);
    expect(screen.getByText(dictionaries.en["gen.searching"])).toBeTruthy();
    cleanup();

    useI18nStore.setState({ locale: "th" });
    render(<SearchingIndicator />);
    expect(screen.getByText(dictionaries.th["gen.searching"])).toBeTruthy();
  });

  it("ImageGeneratingPlaceholder renders per locale with no Thai leak in EN", () => {
    useI18nStore.setState({ locale: "en" });
    const { container } = render(<ImageGeneratingPlaceholder />);
    expect(screen.getByText(dictionaries.en["gen.image"])).toBeTruthy();
    // No Thai codepoints anywhere in the EN render.
    expect(/[฀-๿]/.test(container.textContent ?? "")).toBe(false);
  });
});
