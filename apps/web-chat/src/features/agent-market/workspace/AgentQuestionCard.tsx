"use client";

// A clarification card (kind questions / late_file / output_format).
//
// Open → the app's own QuestionCard, unchanged, plus what only this surface
// knows: each question's evidence and its default ("ค่าแนะนำ …" for a
// blocking question, "ใช้ … ไปก่อนแล้ว" for a cosmetic one), the re-ask
// counter and the note. Any other status renders WITHOUT a single clickable
// answer: interpreting (greyed), answered/defaulted (a compact summary with
// how each answer was given), superseded/cancelled (one muted line).
//
// AM2: a card with a question that takes a typed value (`input`) renders
// TypedQuestionCard instead — the same card whose "something else" box shows
// the hint and checks the value before sending. Evidence that names a page
// or the text read there (E2 cards) also gets a one-line source.
// AM4: every card renders TypedQuestionCard — the same markup, keys and
// answers as the app's card, but always in Thai (the app's card follows the
// app's language).

import { useEffect, useState } from "react";
import { CircleCheck, MessageCircleQuestion } from "lucide-react";

import type { QuestionAnswerPayload } from "@/lib/types";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { toAnswerBody, toPendingQuestion } from "../questions";
import { questionInput } from "../typed";
import type { AnswerBody, AnswerRecord, CardPayload, Question } from "../types";
import { cls, Pill, Spin } from "../ui";
import { Evidence, EvidenceSource } from "./Evidence";
import { FileChip } from "./FileChips";
import { TypedQuestionCard } from "./TypedQuestionCard";

type Phase = "idle" | "sending" | "sent";

const VIA_KEY: Record<string, AmKey> = {
  card: "card.via.card",
  text: "card.via.text",
  default: "card.via.default",
  reprompt_default: "card.via.default",
  remembered: "card.via.remembered",
  upload: "card.via.upload",
  typed: "card.via.typed",
};

export function viaKey(via: string | undefined | null): AmKey | null {
  return via ? VIA_KEY[via] ?? null : null;
}

export function AgentQuestionCard({
  card,
  submit = (body) => amApi.answerCard(card.card_id, body).then(() => undefined),
}: {
  card: CardPayload;
  /** Posts the answer (defaults to POST /cards/{id}/answer). */
  submit?: (body: AnswerBody) => Promise<void>;
}) {
  const t = useAmT();
  const questions = card.questions ?? [];
  const attempts = card.attempts ?? 0;
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [remount, setRemount] = useState(0);

  // A status change (or a re-ask) from the stream supersedes local state.
  useEffect(() => {
    setPhase("idle");
  }, [card.status, attempts]);

  if (card.status === "superseded" || card.status === "cancelled") {
    return <UnusedCard card={card} />;
  }
  if (card.status === "answered" || card.status === "defaulted") {
    return <AnsweredCard card={card} />;
  }
  if (card.status === "interpreting" || phase !== "idle") {
    return (
      <PendingCard
        questions={questions}
        label={card.status === "interpreting" ? t("card.interpreting") : phase === "sending" ? t("card.sending") : t("card.sent")}
      />
    );
  }

  const onAnswer = (answer: QuestionAnswerPayload) => {
    const body = toAnswerBody(questions, answer);
    setPhase("sending");
    setError(null);
    submit(body).then(
      () => setPhase("sent"),
      (err) => {
        setError(errorMessage(err));
        setPhase("idle");
        setRemount((n) => n + 1); // QuestionCard answers once; give it a fresh start
      },
    );
  };

  const rememberable = questions.some((q) => q.rememberable);
  const details = questions.filter((q) => recommendationOf(q) || q.leaves_out || hasEvidence(q));
  const inputs = questions.map((q) => questionInput(q.input));
  const cardKey = `${card.card_id}:${attempts}:${remount}`;

  return (
    <div className="flex flex-col" data-testid="am-question-card" data-card-id={card.card_id}>
      {card.intro ? <p className="mb-1 text-[15px] leading-relaxed text-text-primary">{card.intro}</p> : null}
      {card.files?.length ? (
        <div className="mb-1 flex flex-col gap-1.5">
          <span className="text-xs text-text-muted">{t("card.lateFiles")}</span>
          {card.files.map((f) => (
            <FileChip key={f.id} id={f.id} name={f.name} size={f.size} />
          ))}
        </div>
      ) : null}
      {attempts > 0 ? (
        <div className="mt-1">
          <Pill tone="warning">{t("card.attempt", { n: String(attempts) })}</Pill>
        </div>
      ) : null}
      <TypedQuestionCard key={cardKey} question={toPendingQuestion(card)} inputs={inputs} onAnswer={onAnswer} />
      {details.length ? (
        <ul className="flex flex-col gap-2 rounded-xl border border-border-subtle px-4 py-2.5" data-testid="am-question-details">
          {details.map((q) => (
            <li key={q.key} className="text-xs">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="rounded bg-bg-elevated px-1.5 py-0.5 text-[11px] font-medium text-text-secondary">{q.header}</span>
                {recommendationOf(q) ? (
                  <span className="text-text-secondary" data-testid="am-default-line">
                    {q.severity === "cosmetic"
                      ? t("card.default.cosmetic", { label: recommendationOf(q) })
                      : t("card.recommended", { label: recommendationOf(q) })}
                  </span>
                ) : null}
              </div>
              {q.leaves_out ? (
                <p className="mt-1 text-text-secondary" data-testid="am-skip-line">
                  {t("card.skipLeavesOut")}
                </p>
              ) : null}
              <EvidenceSource evidence={q.evidence} />
              <Evidence evidence={q.evidence} />
            </li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-sm text-status-danger">
          {error}
        </p>
      ) : null}
      {card.note ? <p className="mt-2 text-xs text-text-secondary">{card.note}</p> : null}
      {rememberable ? <p className="mt-1.5 text-xs text-text-muted">{t("card.remember")}</p> : null}
    </div>
  );
}

/** AM5: what the card recommends. What a skipped card records (`default`) is
 *  a recommendation only for a card that does not leave anything out by it:
 *  a card whose skip leaves a document out recommends what it says it
 *  recommends (`recommended_label`), or nothing. */
export function recommendationOf(q: Question): string {
  if (q.recommended_label) return q.recommended_label;
  return q.leaves_out ? "" : q.default_label ?? "";
}

function hasEvidence(q: Question): boolean {
  return !!q.evidence && typeof q.evidence === "object" && Object.keys(q.evidence as object).length > 0;
}

function PendingCard({ questions, label }: { questions: Question[]; label: string }) {
  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-card-pending" aria-busy="true">
      <ul className="flex flex-col gap-1.5">
        {questions.map((q) => (
          <li key={q.key} className="flex flex-wrap items-baseline gap-2 text-sm text-text-muted">
            <span className="rounded bg-bg-base px-1.5 py-0.5 text-[11px] font-medium">{q.header}</span>
            <span>{q.question}</span>
          </li>
        ))}
      </ul>
      <div role="status" className="mt-2.5 flex items-center gap-2 text-xs text-text-secondary">
        <Spin size={13} />
        <span>{label}</span>
      </div>
    </div>
  );
}

function AnsweredCard({ card }: { card: CardPayload }) {
  const t = useAmT();
  const answers: Record<string, AnswerRecord> = card.answer ?? {};
  const fallbackVia = card.status === "defaulted" ? "default" : card.answered_via;
  const filesReceived = card.answered_via === "upload";
  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-card-answered">
      <div className="flex items-center gap-2 text-sm font-medium text-text-primary">
        <CircleCheck width={16} height={16} className="shrink-0 text-status-success" aria-hidden />
        <span>{t("card.answered")}</span>
      </div>
      <ul className="mt-2 flex flex-col gap-1.5">
        {(card.questions ?? []).map((q) => {
          const a = answers[q.key];
          const via = viaKey(a?.via ?? fallbackVia);
          // A request-files card closes itself when the files arrive ({upload:true}).
          const label = a?.label || (filesReceived ? t("card.filesReceived") : null);
          return (
            <li key={q.key} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm" data-testid="am-answer-row">
              <span className="rounded bg-bg-elevated px-1.5 py-0.5 text-[11px] font-medium text-text-secondary">{q.header}</span>
              <span className={cls(label ? "text-text-primary" : "text-text-muted")}>{label || t("card.noAnswer")}</span>
              {via ? <Pill>{t(via)}</Pill> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function UnusedCard({ card }: { card: CardPayload }) {
  const t = useAmT();
  const headers = (card.questions ?? []).map((q) => q.header).filter(Boolean);
  return (
    <div className="flex items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-xs text-text-muted" data-testid="am-card-unused">
      <MessageCircleQuestion width={14} height={14} className="shrink-0" aria-hidden />
      <span>{t("card.unused")}</span>
      {headers.length ? <span className="min-w-0 truncate">{headers.join(", ")}</span> : null}
    </div>
  );
}
