// AM10 F — switching language changes the whole surface.
//
// The ESLint ratchet (scripts/lint-i18n.mjs) sees a literal in JSX; it does
// not see a sentence kept in a lookup table next to the component, a word in
// a `label` prop, or a message captured as text when it happened. These tests
// mount the components that still spoke English in Thai and read what a
// person would read: nothing Latin may be left except the data the test
// passed in and the names the Thai dictionary keeps in English on purpose.

import fs from "node:fs";
import path from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Voice output reaches for speechSynthesis, which jsdom lacks.
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { ConsentCard } from "@/components/chat/ConsentCard";
import { PlanChecklistCard } from "@/components/chat/PlanChecklistCard";
import { SourcesCard } from "@/components/chat/SourcesCard";
import { VideoGeneratingPlaceholder } from "@/components/chat/VideoGeneratingPlaceholder";
import { MediaGenError, type MediaGenErrorCode } from "@/components/media/MediaGenError";
import { ProvenanceBadge } from "@/components/media/ProvenanceBadge";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { PendingConsent } from "@/lib/types";

const APP = path.resolve(__dirname, "..", "..", "..");
const SRC = path.resolve(APP, "src");

/** Latin words (3+ letters) left in a render once `keep` is taken out. */
function englishLeft(text: string, keep: string[] = []): string[] {
  let s = text;
  for (const k of keep) s = s.split(k).join(" ");
  return s.match(/[A-Za-z][A-Za-z'’]{2,}/g) ?? [];
}

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("ConsentCard in Thai", () => {
  const tool: PendingConsent = {
    promptId: "c1",
    tool: "run_command",
    description: "",
    argumentSummary: "",
    argumentDetail: "1",
    dangerClass: "danger.shell",
  };

  it.each([
    "danger.shell",
    "danger.computerUse",
    "danger.code",
    "danger.fileWrite",
    "danger.clipboard",
    "danger.browser",
    "danger.connector",
    "danger.generic",
    "danger.somethingNew",
  ])("a tool request (%s) has no English sentence left", (dangerClass) => {
    const { container } = render(<ConsentCard consent={{ ...tool, dangerClass }} onDecision={() => {}} />);
    expect(englishLeft(container.textContent ?? "", ["run command"])).toEqual([]);
  });

  it.each(["download", "sensitive-info", "authorization"] as const)(
    "a site request that always asks (%s) has no English sentence left",
    (alwaysPromptReason) => {
      const { container } = render(
        <ConsentCard
          consent={{ ...tool, tool: "browser_click", dangerClass: "danger.browser", site: "example.com", alwaysPromptReason }}
          onDecision={() => {}}
        />,
      );
      expect(englishLeft(container.textContent ?? "", ["example.com"])).toEqual([]);
    },
  );

  it("still reads in English when the app is in English", () => {
    useI18nStore.setState({ locale: "en" });
    render(<ConsentCard consent={{ ...tool, site: "example.com", dangerClass: "safe" }} onDecision={() => {}} />);
    expect(screen.getByText("New permissions required")).toBeTruthy();
    expect(screen.getByTestId("consent-deny").textContent).toBe("Decline");
    expect(screen.getByTestId("consent-allow-once").textContent).toBe("Allow this action");
    expect(screen.getByTestId("consent-allow-always").textContent).toBe("Always allow actions on this site");
  });
});

describe("small cards in Thai", () => {
  it("the plan checklist's heading", () => {
    const { container } = render(<PlanChecklistCard steps={[{ text: "ขั้นแรก", status: "in_progress" }]} />);
    expect(englishLeft(container.textContent ?? "")).toEqual([]);
  });

  it.each(["provider_unavailable", "budget_exceeded", "content_policy", "timeout", "unknown"] as MediaGenErrorCode[])(
    "the media error banner (%s)",
    (errorCode) => {
      for (const isAdmin of [false, true]) {
        const { container } = render(
          <MediaGenError errorCode={errorCode} errorMessage="" isAdmin={isAdmin} onRetry={() => {}} onDismiss={() => {}} />,
        );
        expect(englishLeft(container.textContent ?? "")).toEqual([]);
        cleanup();
      }
    },
  );

  it("the video placeholder's cancel button", () => {
    const { container } = render(<VideoGeneratingPlaceholder progressPct={10} onCancel={() => {}} />);
    expect(englishLeft(container.textContent ?? "")).toEqual([]);
  });

  it("the provenance badge and its tooltip", () => {
    const { container } = render(
      <ProvenanceBadge
        provenance={{ provider: "replicate", model_id: "m-1", c2pa_embedded: true, synthid_embedded: true, visible_watermark: true }}
      />,
    );
    // Names of a provider and of two standards stay as they are written.
    const keep = ["Replicate", "C2PA", "SynthID", "m-1"];
    expect(englishLeft(container.textContent ?? "", keep)).toEqual([]);
    const badge = container.querySelector("span[title]") as HTMLElement;
    expect(englishLeft(badge.getAttribute("title") ?? "", keep)).toEqual([]);
    expect(englishLeft(badge.getAttribute("aria-label") ?? "", keep)).toEqual([]);
  });

  it("the sources card's heading and source kinds", () => {
    const at = new Date().toISOString();
    const { container } = render(
      <SourcesCard
        sources={[
          { id: "a", source_type: "saved_memory", source_id: "m", display_label: "ชอบคำตอบสั้น", created_at: at },
          { id: "b", source_type: "conversation_state", display_label: "สถานะ", created_at: at },
          { id: "c", source_type: "past_conversation", source_id: "c9", display_label: "แชทเมื่อวาน", created_at: at },
          { id: "d", source_type: "file_context", display_label: "ไฟล์", created_at: at },
          { id: "e", source_type: "compacted_history", display_label: "ย่อแล้ว", created_at: at },
          { id: "f", source_type: "memory_saved", display_label: "จำไว้แล้ว", created_at: at },
        ]}
        onEditMemory={() => {}}
        onOpenConversation={() => {}}
      />,
    );
    fireEvent.click(container.querySelector("button[aria-expanded]") as HTMLElement);
    expect(englishLeft(container.textContent ?? "")).toEqual([]);
    for (const b of Array.from(container.querySelectorAll("button[aria-label]"))) {
      expect(englishLeft(b.getAttribute("aria-label") ?? "")).toEqual([]);
      expect(englishLeft(b.getAttribute("title") ?? "")).toEqual([]);
    }
  });
});

describe("the dictionary", () => {
  // A Thai value identical to the English one is either a name (it stays)
  // or a word nobody translated. Each one that stays is listed with why.
  const SAME_ON_PURPOSE: Record<string, string> = {
    "voice.method.whisper": "product name",
    "voice.source.openai": "product name",
    "memory.danger.purgePromptPhrase": "the phrase the person types to confirm; compared as typed",
    "settings.web.language.en": "a language is named in itself",
    "settings.web.language.th": "a language is named in itself",
    "connectors.title": "feature name the Thai dictionary keeps",
    "skills.title": "feature name the Thai dictionary keeps",
    "canvas.title": "feature name the Thai dictionary keeps",
    "plan.doc.heading": "heading-level notation (H1, H2)",
    "media.prov.seed": "generation parameter, written as the provider writes it",
  };

  it("has no Thai value left identical to English except the listed names", () => {
    const same = Object.keys(dictionaries.en).filter(
      (k) => dictionaries.en[k as keyof typeof dictionaries.en] === dictionaries.th[k as keyof typeof dictionaries.th],
    );
    expect(same.filter((k) => !(k in SAME_ON_PURPOSE))).toEqual([]);
  });
});

describe("source pins — strings the lint rule cannot see", () => {
  const read = (rel: string) => fs.readFileSync(path.resolve(SRC, rel), "utf8");
  const PINS: Array<{ file: string; forbidden: string[] }> = [
    // A Thai example shown to someone reading English is the same fault.
    { file: "app/chat/skills/page.tsx", forbidden: ['placeholder="สรุป', 'placeholder="ตอบ'] },
    {
      file: "app/chat/tasks/CloudTasksSection.tsx",
      forbidden: ['"Ran"', '"Failed"', '"Paused."', "Paused — reached", "Skipped — ", "`Enable ${", "`Every ${"],
    },
    { file: "components/chat/ConsentCard.tsx", forbidden: ["runs a command on your computer", "download a file"] },
    { file: "components/media/MediaGenError.tsx", forbidden: ["Provider not configured", "Something went wrong"] },
    { file: "components/media/ProvenanceBadge.tsx", forbidden: ["AI-generated by", "visible watermark"] },
    { file: "components/media/MediaModeIndicator.tsx", forbidden: ["Video Generation Mode", "Music Generation Mode"] },
    { file: "components/chat/SourcesCard.tsx", forbidden: ['"saved memory"', '"Edit memory"', '"Open conversation"', '"Not relevant"'] },
    { file: "components/chat/VoiceSettingsModal.tsx", forbidden: ['"Auto-detect (browser locale)"', '"Japanese"', '"Done"'] },
    {
      file: "stores/chatStore.ts",
      forbidden: ["\"Couldn't load conversations.\"", "\"Couldn't update pin. Please try again.\"", "\"Couldn't switch branch.\""],
    },
    { file: "stores/knowledgeStore.ts", forbidden: ['"Failed to load"'] },
    { file: "hooks/useVoiceInput.ts", forbidden: ["`Voice input error: ${"] },
    { file: "hooks/useWhisperInput.ts", forbidden: ['"Transcription failed."'] },
  ];
  it.each(PINS)("$file", ({ file, forbidden }) => {
    const src = read(file);
    for (const f of forbidden) expect(src.includes(f), `${file} still has ${f}`).toBe(false);
  });
});
