"use client";

// Small "AI-generated" transparency badge shown on generated media.
// Reads provider-disclosed provenance (C2PA / watermark flags, model id)
// from the job-status response (output_metadata.provenance, surfaced by the
// backend in Sprint 5). Purely informational — no network calls.

import { Info, ShieldCheck } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { MediaProvenance } from "@/lib/types";

interface Props {
  provenance: MediaProvenance;
  className?: string;
}

function providerLabel(provider: string): string {
  const map: Record<string, string> = {
    replicate: "Replicate",
    openai: "OpenAI",
    elevenlabs: "ElevenLabs",
    mock: "Mock",
  };
  return map[provider] ?? provider;
}

export function ProvenanceBadge({ provenance, className }: Props) {
  const t = useT();
  const { provider, model_id, c2pa_embedded, synthid_embedded, visible_watermark } =
    provenance;

  const verified = Boolean(c2pa_embedded || synthid_embedded);
  const flags: string[] = [];
  if (c2pa_embedded) flags.push(t("media.prov.c2pa"));
  if (synthid_embedded) flags.push(t("media.prov.synthid"));
  if (visible_watermark) flags.push(t("media.prov.visible"));
  if (typeof provenance.generation_seed === "number")
    flags.push(t("media.prov.seed", { n: String(provenance.generation_seed) }));

  const tooltip =
    t("media.prov.tooltip", { provider: providerLabel(provider), model: model_id }) +
    (flags.length ? ` ${t("media.prov.provenance", { flags: flags.join(", ") })}` : "");

  return (
    <span
      title={tooltip}
      aria-label={tooltip}
      className={
        "inline-flex items-center gap-1 rounded-full border border-border " +
        "bg-bg-elevated px-2 py-0.5 text-[11px] text-text-muted " +
        "backdrop-blur " +
        (className ?? "")
      }
    >
      {verified ? (
        <ShieldCheck className="h-3 w-3" aria-hidden />
      ) : (
        <Info className="h-3 w-3" aria-hidden />
      )}
      <span>{t("media.prov.aiGenerated")} · {providerLabel(provider)}</span>
      {verified ? <span className="text-text-secondary">· C2PA</span> : null}
    </span>
  );
}
