"use client";

// FIX73 (desktop shell) — the in-thread question card, the visible half of
// the bridge's question.request (the model's ask_user_question tool). One to
// four questions, each with 2–4 options the MODEL wrote, plus the two rows
// the CLIENT adds: a "Something else" choice with a text box, and a free
// reply typed instead of answering. The host owns the turn; this only
// collects the answer and posts question.respond via resolveQuestion.
//
// What goes back: the chosen label(s) verbatim, or the user's own text — the
// word "Other" never travels. Skip means "the user declined to choose", not
// a cancel: the turn continues and the model is told to proceed on its own.

import { MessageCircleQuestion } from "lucide-react";
import { useState } from "react";

import { useT } from "@/lib/i18n";
import type { PendingQuestion, QuestionAnswerPayload } from "@/lib/types";

const OTHER = "__other__";
const MAX_FREE_TEXT = 2000;

export function QuestionCard({
  question,
  onAnswer,
}: {
  question: PendingQuestion;
  onAnswer: (answer: QuestionAnswerPayload) => void;
}) {
  const t = useT();
  const [picked, setPicked] = useState<Record<number, string[]>>({});
  const [otherText, setOtherText] = useState<Record<number, string>>({});
  const [reply, setReply] = useState("");
  const [sent, setSent] = useState(false);

  const toggle = (qi: number, label: string, multi: boolean) => {
    setPicked((p) => {
      const cur = p[qi] ?? [];
      if (multi) return { ...p, [qi]: cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label] };
      return { ...p, [qi]: cur.includes(label) && label !== OTHER ? [] : [label] };
    });
  };

  const answerOf = (qi: number): string[] => {
    const cur = picked[qi] ?? [];
    const out = cur.filter((l) => l !== OTHER);
    if (cur.includes(OTHER)) {
      const txt = (otherText[qi] ?? "").trim();
      if (txt) out.push(txt.slice(0, MAX_FREE_TEXT));
    }
    return out;
  };

  const replyText = reply.trim().slice(0, MAX_FREE_TEXT);
  const allAnswered = question.questions.every((_, qi) => answerOf(qi).length > 0);
  const canSubmit = !sent && (replyText.length > 0 || allAnswered);

  const submit = () => {
    if (!canSubmit) return;
    setSent(true);
    const answers: Record<string, string[]> = {};
    question.questions.forEach((q, qi) => {
      const a = answerOf(qi);
      if (a.length) answers[q.question] = a;
    });
    onAnswer({ promptId: question.promptId, outcome: "answered", answers, response: replyText || null });
  };
  const skip = () => {
    if (sent) return;
    setSent(true);
    onAnswer({ promptId: question.promptId, outcome: "skipped", answers: {}, response: null });
  };

  return (
    <div
      className="my-2 rounded-lg border border-blue-500/50 bg-blue-500/5 px-3 py-3"
      data-testid="question-card"
      data-prompt-id={question.promptId}
    >
      <div className="flex items-center gap-2">
        <MessageCircleQuestion className="h-5 w-5 shrink-0 text-blue-500" />
        <div className="text-sm font-semibold text-text-primary">{t("question.title")}</div>
      </div>

      {question.questions.map((q, qi) => {
        const cur = picked[qi] ?? [];
        const otherOn = cur.includes(OTHER);
        return (
          <fieldset key={qi} className="mt-3" data-testid="question-item" disabled={sent}>
            <legend className="flex items-center gap-2 text-sm text-text-primary">
              <span className="rounded bg-bg-base px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-text-tertiary" data-testid="question-header">
                {q.header}
              </span>
              <span className="font-medium">{q.question}</span>
            </legend>
            {q.multiSelect ? <div className="mt-0.5 text-xs text-text-tertiary">{t("question.multiHint")}</div> : null}
            <div className="mt-1.5 flex flex-col gap-1">
              {q.options.map((o) => {
                const on = cur.includes(o.label);
                return (
                  <button
                    key={o.label}
                    type="button"
                    role={q.multiSelect ? "checkbox" : "radio"}
                    aria-checked={on}
                    data-testid="question-option"
                    onClick={() => toggle(qi, o.label, q.multiSelect)}
                    className={`focus-ring flex w-full flex-col items-start rounded-md border px-3 py-1.5 text-left text-sm ${on ? "border-blue-500 bg-blue-500/10 text-text-primary" : "border-border text-text-primary hover:bg-bg-surface-hover"}`}
                  >
                    <span className="font-medium">{o.label}</span>
                    {o.description ? <span className="text-xs text-text-secondary">{o.description}</span> : null}
                  </button>
                );
              })}
              <button
                type="button"
                role={q.multiSelect ? "checkbox" : "radio"}
                aria-checked={otherOn}
                data-testid="question-other"
                onClick={() => toggle(qi, OTHER, q.multiSelect)}
                className={`focus-ring flex w-full flex-col items-start rounded-md border border-dashed px-3 py-1.5 text-left text-sm ${otherOn ? "border-blue-500 bg-blue-500/10 text-text-primary" : "border-border text-text-secondary hover:bg-bg-surface-hover"}`}
              >
                <span className="font-medium">{t("question.other")}</span>
              </button>
              {otherOn ? (
                <input
                  type="text"
                  autoFocus
                  maxLength={MAX_FREE_TEXT}
                  value={otherText[qi] ?? ""}
                  onChange={(e) => setOtherText((p) => ({ ...p, [qi]: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                  placeholder={t("question.otherPlaceholder")}
                  data-testid="question-other-text"
                  className="focus-ring w-full rounded-md border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary"
                />
              ) : null}
            </div>
          </fieldset>
        );
      })}

      <div className="mt-3">
        <label className="text-xs text-text-tertiary" htmlFor={`question-reply-${question.promptId}`}>
          {t("question.replyLabel")}
        </label>
        <textarea
          id={`question-reply-${question.promptId}`}
          rows={1}
          maxLength={MAX_FREE_TEXT}
          value={reply}
          disabled={sent}
          onChange={(e) => setReply(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={t("question.replyPlaceholder")}
          data-testid="question-reply"
          className="focus-ring mt-1 w-full resize-none rounded-md border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary"
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto text-xs text-text-tertiary">{t("question.skipHint")}</span>
        <button
          type="button"
          onClick={skip}
          disabled={sent}
          className="focus-ring rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-bg-surface-hover disabled:opacity-50"
          data-testid="question-skip"
        >
          {t("question.skip")}
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="focus-ring rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="question-submit"
        >
          {t("question.submit")}
        </button>
      </div>
    </div>
  );
}
