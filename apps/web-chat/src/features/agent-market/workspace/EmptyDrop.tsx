"use client";

// AM4 — an empty workspace says, in one sentence, exactly what to drop in,
// and that sentence is the loudest thing on the page. The rest of what the
// agent takes (optional files) follows in small print.

import { FileUp } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { tAm, useAmT, type AmT } from "../i18n";
import type { Manifest } from "../types";
import { EstimateLine } from "./Estimate";
import { UploadInfo } from "./UploadInfo";

/** "ส่ง<the first required input>มาได้เลย" / "To start, send: <…>" — the label is the
 *  agent's own (its manifest); the sentence around it is the dictionary's. A
 *  component passes its own `t`, so the sentence follows a change of language. */
export function dropSentence(manifest: Manifest | null | undefined, t: AmT = tAm): string | null {
  const inputs = manifest?.inputs ?? [];
  const main = inputs.find((i) => i.required) ?? inputs[0];
  if (!main?.label) return null;
  return t("empty.send", { label: main.label.trim() });
}

export function EmptyDrop({
  manifest,
  onPick,
  estimate,
}: {
  manifest: Manifest | null | undefined;
  onPick: () => void;
  /** AM6: what a round costs, in the server's sentence (absent: not known, nothing is said). */
  estimate?: string | null;
}) {
  const t = useAmT();
  const sentence = dropSentence(manifest, t) ?? t("empty.fallback");
  const inputs = manifest?.inputs ?? [];
  const main = inputs.find((i) => i.required) ?? inputs[0];
  const optional = inputs.filter((i) => i !== main && i.label);
  return (
    <section
      aria-label={sentence}
      className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-border-strong bg-bg-surface px-5 py-8 text-center"
      data-testid="am-empty-drop"
    >
      <FileUp width={32} height={32} className="text-text-muted" aria-hidden />
      <h2 className="max-w-xl text-lg font-semibold leading-snug text-text-primary sm:text-xl" data-testid="am-empty-sentence">
        {sentence}
      </h2>
      {main?.note ? <p className="max-w-xl text-sm text-text-secondary">{main.note}</p> : null}
      <div className="flex flex-col items-center gap-1.5">
        <Button type="button" onClick={onPick} data-testid="am-empty-pick">
          {t("empty.pick")}
        </Button>
        <span className="text-xs text-text-muted">{t("empty.drop")}</span>
      </div>
      <EstimateLine line={estimate} center />
      {optional.length ? (
        <p className="max-w-xl text-xs text-text-muted">
          {`${t("common.optional")}: ${optional.map((i) => i.label).join(" · ")}`}
        </p>
      ) : null}
      <UploadInfo manifest={manifest} center />
    </section>
  );
}
