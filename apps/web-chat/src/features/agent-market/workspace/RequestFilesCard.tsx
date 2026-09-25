"use client";

// A request_files card: the run is parked until a file arrives. Each question
// lists the files asked for (label, why, required) and has its own upload
// slot; the uploaded files go out as a message so the parked run resumes.
// The options become buttons — except "wait", which is what uploading IS.

import { useEffect, useRef, useState } from "react";
import { FileUp } from "lucide-react";

import { amApi, errorMessage, newClientId } from "../api";
import { useAmT } from "../i18n";
import { isWaitOption, toAnswerBody } from "../questions";
import type { AnswerBody, CardPayload, Question } from "../types";
import { BTN_SECONDARY, cls, Pill, Spin } from "../ui";
import { AgentQuestionCard } from "./AgentQuestionCard";
import { useWorkspace } from "./context";
import { StagedChip } from "./FileChips";
import { DEFAULT_ACCEPT, useUploads } from "./useUploads";

export function RequestFilesCard({
  card,
  submit = (body) => amApi.answerCard(card.card_id, body).then(() => undefined),
}: {
  card: CardPayload;
  submit?: (body: AnswerBody) => Promise<void>;
}) {
  if (card.status !== "open") return <AgentQuestionCard card={card} submit={submit} />;
  return (
    <div className="flex flex-col gap-2" data-testid="am-request-files" data-card-id={card.card_id}>
      {card.intro ? <p className="text-[15px] leading-relaxed text-text-primary">{card.intro}</p> : null}
      {(card.questions ?? []).map((q) => (
        <RequestSlot key={q.key} q={q} attempts={card.attempts ?? 0} submit={submit} />
      ))}
      {card.note ? <p className="text-xs text-text-secondary">{card.note}</p> : null}
    </div>
  );
}

function RequestSlot({ q, attempts, submit }: { q: Question; attempts: number; submit: (body: AnswerBody) => Promise<void> }) {
  const t = useAmT();
  const ws = useWorkspace();
  const accept = unionAccept(q) ?? ws?.accept ?? DEFAULT_ACCEPT;
  const uploads = useUploads(ws?.installId ?? null, { accept, maxMb: ws?.maxMb });
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [phase, setPhase] = useState<"idle" | "sending" | "sent" | "answering">("idle");
  const [error, setError] = useState<string | null>(null);
  const clientId = useRef(newClientId());
  const sentFor = useRef<string>("");

  // Asked again (the files did not satisfy the check): start the slot over.
  const clearUploads = uploads.clear;
  useEffect(() => {
    if (attempts === 0) return;
    setPhase("idle");
    clearUploads();
    clientId.current = newClientId();
    sentFor.current = "";
  }, [attempts, clearUploads]);

  // Once every picked file is stored, send them: the parked run resumes.
  const readyIds = uploads.ids.join(",");
  useEffect(() => {
    if (!ws || phase !== "idle" || uploads.uploading || !readyIds || readyIds === sentFor.current) return;
    sentFor.current = readyIds;
    setPhase("sending");
    setError(null);
    amApi
      .message(ws.installId, { text: "", file_ids: uploads.ids, client_id: clientId.current })
      .then(
        (res) => {
          if (res?.item) ws.merge([res.item]);
          setPhase("sent");
        },
        (err) => {
          setError(errorMessage(err));
          setPhase("idle");
          sentFor.current = "";
        },
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyIds, uploads.uploading, phase, ws]);

  const choose = (label: string) => {
    setPhase("answering");
    setError(null);
    const body = toAnswerBody([q], { promptId: "", outcome: "answered", answers: { [q.question]: [label] }, response: null });
    submit(body).then(
      () => setPhase("sent"),
      (err) => {
        setError(errorMessage(err));
        setPhase("idle");
      },
    );
  };

  const busy = phase !== "idle";
  const buttons = (q.options ?? []).filter((o) => !isWaitOption(o.value));

  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-bg-elevated px-1.5 py-0.5 text-[11px] font-medium text-text-secondary">{q.header}</span>
        {attempts > 0 ? <Pill tone="warning">{t("card.attempt", { n: String(attempts) })}</Pill> : null}
      </div>
      <p className="mt-1.5 text-sm font-medium text-text-primary">{q.question}</p>
      {q.request?.length ? (
        <ul className="mt-2 flex flex-col gap-1.5">
          {q.request.map((r, i) => (
            <li key={`${r.label}-${i}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
              <span className="text-text-primary">{r.label}</span>
              <Pill tone={r.required ? "warning" : "neutral"}>{r.required ? t("common.required") : t("common.optional")}</Pill>
              {r.why ? <span className="w-full text-xs text-text-secondary">{r.why}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      <div
        className={cls(
          "mt-3 flex flex-col items-center gap-1.5 rounded-lg border border-dashed px-3 py-4 text-center text-sm transition-colors",
          over ? "border-border-strong bg-bg-elevated" : "border-border bg-bg-base",
        )}
        onDragEnter={(e) => {
          if (busy || !hasFiles(e)) return;
          e.preventDefault();
          setOver(true);
        }}
        onDragOver={(e) => {
          if (busy || !hasFiles(e)) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (busy) return;
          const files = Array.from(e.dataTransfer.files ?? []);
          if (!files.length) return;
          // The workspace's own drop zone sees this drop too (it must, to
          // clear its overlay): tell it these files are taken.
          ws?.claimDrop?.();
          uploads.add(files);
        }}
        data-testid="am-request-drop"
      >
        <FileUp width={20} height={20} className="text-text-muted" aria-hidden />
        <div className="text-text-secondary">
          <span>{t("req.drop")} </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className="focus-ring rounded font-medium text-text-primary underline underline-offset-2 hover:opacity-80 disabled:opacity-50"
          >
            {t("req.choose")}
          </button>
        </div>
        <div className="text-[11px] text-text-muted">{t("req.accept", { types: accept.join(" ") })}</div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={accept.join(",")}
          className="hidden"
          aria-label={t("req.uploadFor", { label: q.request?.[0]?.label ?? q.header })}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (files.length) uploads.add(files);
          }}
        />
      </div>

      {uploads.items.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {uploads.items.map((s) => (
            <StagedChip key={s.localId} item={s} onRemove={() => uploads.remove(s.localId)} />
          ))}
        </div>
      ) : null}

      {phase === "sending" || phase === "sent" || phase === "answering" ? (
        <div role="status" className="mt-2 flex items-center gap-2 text-xs text-text-secondary">
          {phase === "sent" ? null : <Spin size={13} />}
          <span>{phase === "sent" ? t("req.sent") : phase === "sending" ? t("req.sending") : t("card.sending")}</span>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-sm text-status-danger">
          {error}
        </p>
      ) : null}

      {buttons.length ? (
        <div className="mt-3 flex flex-wrap gap-2" data-testid="am-request-options">
          {buttons.map((o) => (
            <button key={o.label} type="button" disabled={busy} onClick={() => choose(o.label)} className={BTN_SECONDARY} title={o.description || undefined}>
              {o.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function unionAccept(q: Question): string[] | null {
  const all = (q.request ?? []).flatMap((r) => r.accept ?? []);
  return all.length ? [...new Set(all.map((a) => a.toLowerCase()))] : null;
}

function hasFiles(e: React.DragEvent): boolean {
  const types = e.dataTransfer?.types;
  if (!types) return false;
  for (let i = 0; i < types.length; i++) if (types[i] === "Files") return true;
  return false;
}
