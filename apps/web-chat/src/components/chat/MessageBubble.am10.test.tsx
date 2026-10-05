// AM10 F — a message already on screen follows a language switch, and the
// baseline of hard-coded strings holds no sentence a person reads.
//
// The message bubble's parts were moved by AM5 (MessageParts.tsx), so its
// share of the language sweep is a commit of the feature and leaves with it:
// the failed-turn card re-says its sentence from the error's code in the
// language on screen now (the one the store captured when the frame arrived
// stayed in the old language), the paused card, the fallback notice and the
// unit words go through the dictionary, and the three literals the bubble
// kept leave the baseline.

import fs from "node:fs";
import path from "node:path";

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Voice output reaches for speechSynthesis, which jsdom lacks.
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

const APP = path.resolve(__dirname, "..", "..", "..");

/** Latin words (3+ letters) left in a render once `keep` is taken out. */
function englishLeft(text: string, keep: string[] = []): string[] {
  let s = text;
  for (const k of keep) s = s.split(k).join(" ");
  return s.match(/[A-Za-z][A-Za-z'’]{2,}/g) ?? [];
}

function msg(partial: Partial<Message>): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "",
    created_at: new Date().toISOString(),
    status: "completed",
    ...partial,
  } as Message;
}

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("a message already on screen follows a language switch", () => {
  it("the paused card", () => {
    const { container } = render(<MessageBubble convId="c1" message={msg({ content: "ก", paused: { reason: "budget" } })} />);
    const card = container.querySelector('[data-testid="paused-card"]') as HTMLElement;
    expect(englishLeft(card.textContent ?? "")).toEqual([]);
  });

  it("an error captured in English is shown in Thai after the switch, and back", () => {
    useI18nStore.setState({ locale: "en" });
    render(
      <MessageBubble
        convId="c1"
        message={msg({
          status: "error",
          error_code: "provider_error",
          // What the store captured when the frame arrived, in English.
          error_message: dictionaries.en["error.provider_error"],
        })}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.en["error.provider_error"]);
    act(() => useI18nStore.setState({ locale: "th" }));
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.th["error.provider_error"]);
    expect(screen.getByRole("alert").textContent).not.toContain(dictionaries.en["error.provider_error"]);
    act(() => useI18nStore.setState({ locale: "en" }));
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.en["error.provider_error"]);
  });

  it("an unknown code's neutral line follows the switch too", () => {
    useI18nStore.setState({ locale: "en" });
    render(
      <MessageBubble
        convId="c1"
        message={msg({ status: "error", error_code: "never_heard_of_it", error_message: dictionaries.en["error.unknownNoRetry"] })}
      />,
    );
    act(() => useI18nStore.setState({ locale: "th" }));
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.th["error.unknownNoRetry"]);
  });

  it("a server sentence for an unknown code is shown as received — it is not ours to translate", () => {
    useI18nStore.setState({ locale: "en" });
    render(
      <MessageBubble
        convId="c1"
        message={msg({ status: "error", error_code: "never_heard_of_it", error_message: "The server said this." })}
      />,
    );
    act(() => useI18nStore.setState({ locale: "th" }));
    expect(screen.getByRole("alert").textContent).toContain("The server said this.");
  });

  it("the fallback notice", () => {
    useI18nStore.setState({ locale: "en" });
    render(
      <MessageBubble
        convId="c1"
        message={msg({ content: "ก", fallback_info: { requested_model: "model-a", actual_model: "model-b" } as never })}
      />,
    );
    const en = dictionaries.en["error.fallbackNotice"].replace(/\{requested\}/g, "model-a").replace(/\{actual\}/g, "model-b");
    const th = dictionaries.th["error.fallbackNotice"].replace(/\{requested\}/g, "model-a").replace(/\{actual\}/g, "model-b");
    expect(screen.getByTestId("fallback-notice").textContent).toBe(en);
    act(() => useI18nStore.setState({ locale: "th" }));
    expect(screen.getByTestId("fallback-notice").textContent).toBe(th);
  });
});

describe("the hard-coded string baseline", () => {
  const baseline = JSON.parse(fs.readFileSync(path.resolve(APP, "i18n-baseline.json"), "utf8")) as {
    count: number;
    keys: string[];
  };

  // What may stay: text that is not a sentence for a person to read in their
  // language — a brand, a file format, the name of a standard, a version tag,
  // an example of something typed as code. Everything else goes through the
  // dictionary (a unit too: the tool card's milliseconds are said in Thai).
  const MAY_STAY: Array<{ file: string; has: string; why: string }> = [
    { file: "src/app/chat/settings/DeviceSettings.tsx", has: "chrome://extensions", why: "an address typed into Chrome" },
    { file: "src/app/chat/skills/page.tsx", has: 'placeholder="summarize-short"', why: "example of a skill id (an identifier)" },
    { file: "src/app/shared/[token]/page.tsx", has: "SRAN Chat", why: "brand" },
    { file: "src/components/chat/EmptyState.tsx", has: 'alt="SRAN Chat"', why: "brand" },
    { file: "src/components/chat/Sidebar.tsx", has: 'aria-label="SRAN Chat"', why: "brand" },
    { file: "src/components/chat/Sidebar.tsx", has: ">Chat</span>", why: "second word of the brand's wordmark" },
    { file: "src/components/chat/AttachmentDropZone.tsx", has: "PDF · DOCX · XLSX", why: "file format names" },
    { file: "src/components/chat/ExcelPlanCard.tsx", has: "<span>Excel</span>", why: "product name" },
    { file: "src/components/media/VideoGenForm.tsx", has: "720p", why: "a resolution" },
    { file: "src/components/media/VideoGenForm.tsx", has: "1080p", why: "a resolution" },
    { file: "src/components/media/ProvenanceBadge.tsx", has: "C2PA", why: "name of a standard" },
    { file: "src/components/canvas/CanvasPanel.tsx", has: "v{v.version}", why: "version tag" },
    { file: "src/components/workspace/VersionTimeline.tsx", has: "v{v.version}", why: "version tag" },
    { file: "src/components/workspace/WorkspacePanel.tsx", has: "v{f.version}", why: "version tag" },
  ];

  it("holds only entries that are not sentences for a person", () => {
    const unexplained = baseline.keys.filter((k) => !MAY_STAY.some((m) => k.startsWith(m.file + "::") && k.includes(m.has)));
    expect(unexplained).toEqual([]);
  });

  it("gives no reason for an entry that is not there", () => {
    const unused = MAY_STAY.filter((m) => !baseline.keys.some((k) => k.startsWith(m.file + "::") && k.includes(m.has)));
    expect(unused).toEqual([]);
  });

  it("counts what it lists", () => {
    expect(baseline.count).toBe(baseline.keys.length);
  });
});
