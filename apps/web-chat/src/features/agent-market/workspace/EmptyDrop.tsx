"use client";

// AM4 — an empty workspace says, in one sentence, exactly what to drop in,
// and that sentence is the loudest thing on the page. The rest of what the
// agent takes (optional files) follows in small print.

import { FileUp } from "lucide-react";

import { useAmT } from "../i18n";
import type { Manifest } from "../types";
import { BTN_PRIMARY } from "../ui";
import { UploadInfo } from "./UploadInfo";

/** "ส่ง<the first required input>มาได้เลย" — from the agent's own manifest. */
export function dropSentence(manifest: Manifest | null | undefined): string | null {
  const inputs = manifest?.inputs ?? [];
  const main = inputs.find((i) => i.required) ?? inputs[0];
  if (!main?.label) return null;
  return `ส่ง${main.label.trim()}มาได้เลย`;
}

export function EmptyDrop({ manifest, onPick }: { manifest: Manifest | null | undefined; onPick: () => void }) {
  const t = useAmT();
  const sentence = dropSentence(manifest) ?? t("empty.fallback");
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
        <button type="button" onClick={onPick} className={BTN_PRIMARY} data-testid="am-empty-pick">
          {t("empty.pick")}
        </button>
        <span className="text-xs text-text-muted">{t("empty.drop")}</span>
      </div>
      {optional.length ? (
        <p className="max-w-xl text-xs text-text-muted">
          {`${t("common.optional")}: ${optional.map((i) => i.label).join(" · ")}`}
        </p>
      ) : null}
      <UploadInfo manifest={manifest} center />
    </section>
  );
}
