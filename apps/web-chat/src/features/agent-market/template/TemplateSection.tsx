"use client";

// Settings → "แบบฟอร์มของคุณ": the customer's own template (AM2 Phase E),
// shown only for an agent whose manifest has a templates block. The saved
// template (name, version, when) with "replace with a new version" and "stop
// using a template"; with none, an upload button and what a template does.
// An upload (POST /installs/{id}/template) starts an inspect job; its step
// "template:<file>" holds the slots, the proposal and the diff against the
// saved version, and the mapping screen takes it from there.

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { Spinner } from "@/components/ui/Spinner";

import { AmError, amApi, errorMessage } from "../api";
import { tAm, useAmLocale, useAmT } from "../i18n";
import { formatDate } from "../format";
import type { SavedTemplate, TemplateInspect, TemplateSpec } from "../types";
import { ErrorPanel } from "../ui";
import { mappedCount } from "./mapping";
import { TemplateMapper } from "./TemplateMapper";

export const TEMPLATE_ACCEPT = [".docx", ".xlsx", ".xlsm", ".pptx"];
const POLL_MS = 700;
// AM8: reading a form now includes a model's proposal, which waits while a chat turn is generating
const POLL_LIMIT_MS = 180_000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Wait for the inspect job and return its step's output (the step whose name starts with "template:"). */
export async function pollTemplate(jobId: string, alive: () => boolean, pollMs = POLL_MS): Promise<TemplateInspect> {
  const deadline = Date.now() + POLL_LIMIT_MS;
  while (Date.now() < deadline) {
    await sleep(pollMs);
    if (!alive()) throw new AmError("ABORTED", "", 0);
    const d = await amApi.run(jobId);
    const status = d?.run?.status;
    if (status === "succeeded") {
      const step = (d.steps ?? []).find((s) => s.name.startsWith("template:"));
      const out = step?.output as TemplateInspect | undefined;
      if (!out || !Array.isArray(out.slots)) throw new AmError("TEMPLATE_EMPTY", tAm("tpl.readFailed"), 0);
      return { ...out, proposal: out.proposal ?? {}, diff: out.diff ?? { first: true, kept: [], added: [], removed: [] } };
    }
    if (status === "failed" || status === "cancelled") throw new AmError("TEMPLATE_FAILED", tAm("tpl.readFailed"), 0);
  }
  throw new AmError("TEMPLATE_TIMEOUT", tAm("tpl.readTimeout"), 0);
}

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; name: string; pct: number }
  | { kind: "reading"; name: string }
  | { kind: "mapping"; fileId: string; name: string; inspect: TemplateInspect }
  | { kind: "error"; message: string };

export function TemplateSection({
  installId,
  spec,
  saved,
  onChanged,
  pollMs,
}: {
  installId: string;
  spec: TemplateSpec;
  saved: SavedTemplate | null;
  onChanged: () => void;
  /** Tests only: a faster poll. */
  pollMs?: number;
}) {
  const t = useAmT();
  const locale = useAmLocale();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const busy = phase.kind === "uploading" || phase.kind === "reading" || removing;

  const take = async (file: File) => {
    const dot = file.name.lastIndexOf(".");
    const ext = dot < 0 ? "" : file.name.slice(dot).toLowerCase();
    if (!TEMPLATE_ACCEPT.includes(ext)) {
      setPhase({ kind: "error", message: t("error.fileType", { types: TEMPLATE_ACCEPT.join(" ") }) });
      return;
    }
    setConfirming(false);
    setPhase({ kind: "uploading", name: file.name, pct: 0 });
    try {
      const up = await amApi.uploadTemplate(installId, file, {
        onProgress: (pct) => alive.current && setPhase((p) => (p.kind === "uploading" ? { ...p, pct } : p)),
      });
      if (!alive.current) return;
      setPhase({ kind: "reading", name: file.name });
      const inspect = await pollTemplate(up.job.id, () => alive.current, pollMs);
      if (!alive.current) return;
      setPhase({ kind: "mapping", fileId: up.file.id, name: up.file.name || file.name, inspect });
    } catch (err) {
      if (!alive.current || (err instanceof AmError && err.code === "ABORTED")) return;
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  };

  const remove = async () => {
    setRemoving(true);
    try {
      await amApi.removeTemplate(installId);
      setConfirming(false);
      setPhase({ kind: "idle" });
      onChanged();
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    } finally {
      setRemoving(false);
    }
  };

  return (
    <section className="mt-8" aria-labelledby="am-template-title">
      <h2 id="am-template-title" className="text-sm font-semibold text-text-primary">
        {t("tpl.title")}
      </h2>
      <div className="mt-2.5 rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-template">
        {phase.kind === "mapping" ? (
          <TemplateMapper
            installId={installId}
            spec={spec}
            fileId={phase.fileId}
            fileName={phase.name}
            inspect={phase.inspect}
            saved={saved}
            onSaved={() => {
              setPhase({ kind: "idle" });
              onChanged();
            }}
            onCancel={() => setPhase({ kind: "idle" })}
          />
        ) : (
          <>
            {saved ? (
              <div className="flex items-center gap-3" data-testid="am-template-saved">
                <FileTypeBadge filename={saved.name} size="md" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-text-primary" title={saved.name}>
                    {saved.name}
                  </div>
                  <div className="text-xs text-text-muted">
                    {t("tpl.savedLine", { v: String(saved.version), when: formatDate(saved.saved_at, locale) })}
                    {` · ${t("tpl.mappedLine", { n: String(mappedCount(saved.slots ?? [], saved.mapping)) })}`}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-text-secondary" data-testid="am-template-explain">
                {t("tpl.explain")}
              </p>
            )}

            <div aria-live="polite">
              {phase.kind === "uploading" || phase.kind === "reading" ? (
                <div role="status" className="mt-3 flex items-center gap-2 text-xs text-text-secondary">
                  <Spinner size={13} />
                  <span className="min-w-0 truncate">
                    {`${phase.name} · ${phase.kind === "uploading" ? t("tpl.uploading", { pct: String(phase.pct) }) : t("tpl.reading")}`}
                  </span>
                </div>
              ) : null}
              {phase.kind === "error" ? (
                <div className="mt-3">
                  <ErrorPanel message={phase.message} />
                </div>
              ) : null}
            </div>

            {confirming ? (
              <div className="mt-3 rounded-lg border border-status-danger/40 bg-status-danger/10 px-3 py-3" role="alertdialog" aria-labelledby="am-template-stop-q">
                <p id="am-template-stop-q" className="text-sm text-text-primary">
                  {t("tpl.stopConfirm")}
                </p>
                <div className="mt-2.5 flex flex-wrap gap-2">
                  <Button type="button" size="sm" onClick={() => void remove()} loading={removing} data-testid="am-template-stop-yes">
                    <span>{removing ? t("tpl.stopping") : t("tpl.stopYes")}</span>
                  </Button>
                  <Button type="button" variant="secondary" size="sm" onClick={() => setConfirming(false)} disabled={removing}>
                    {t("common.cancel")}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={saved ? "secondary" : "primary"}
                  size="sm"
                  onClick={() => inputRef.current?.click()}
                  disabled={busy}
                  data-testid="am-template-upload"
                >
                  {saved ? t("tpl.replace") : t("tpl.upload")}
                </Button>
                {saved ? (
                  <Button type="button" variant="secondary" size="sm" onClick={() => setConfirming(true)} disabled={busy} data-testid="am-template-stop">
                    {t("tpl.stop")}
                  </Button>
                ) : null}
              </div>
            )}
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={TEMPLATE_ACCEPT.join(",")}
          className="hidden"
          aria-label={saved ? t("tpl.replace") : t("tpl.upload")}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void take(f);
          }}
          data-testid="am-template-file"
        />
      </div>
    </section>
  );
}
