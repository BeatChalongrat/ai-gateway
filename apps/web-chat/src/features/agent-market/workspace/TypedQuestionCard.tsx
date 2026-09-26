"use client";

// AM2 — a card whose questions take a typed value (`input: {kind, hint}`,
// the invoice agent's E2 cards: an amount, a tax ID, a date, a rate, text).
//
// The app's QuestionCard (@/components/chat/QuestionCard) as it looks and
// answers — same markup, test ids, number keys, Enter, Skip and free reply,
// same QuestionAnswerPayload out, so toAnswerBody() is unchanged — with one
// difference only this surface has: the "Something else" box of a question
// with an input shows the question's hint and checks the value first
// (typed.ts). An invalid value gets an inline reason and the card does not
// send until it is fixed; the server checks again. The shared card is not
// changed (it also serves the desktop's ask_user_question); a card without
// any input keeps using it.

import { MessageCircleQuestion } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useT } from "@/lib/i18n";
import type { PendingQuestion, QuestionAnswerPayload } from "@/lib/types";

import { useAmT, type AmKey } from "../i18n";
import { checkTyped } from "../typed";
import type { InputKind } from "../types";
import { cls } from "../ui";

const OTHER = "__other__";
const MAX_FREE_TEXT = 2000;

export type CardInput = { kind: InputKind; hint: string } | null;

export function TypedQuestionCard({
  question,
  inputs,
  onAnswer,
}: {
  question: PendingQuestion;
  /** Per question (same order): its typed input, or null. */
  inputs: CardInput[];
  onAnswer: (answer: QuestionAnswerPayload) => void;
}) {
  const t = useT();
  const [picked, setPicked] = useState<Record<number, string[]>>({});
  const [otherText, setOtherText] = useState<Record<number, string>>({});
  const [left, setLeft] = useState<Record<number, boolean>>({});
  const [tried, setTried] = useState(false);
  const [reply, setReply] = useState("");
  const [replyOpen, setReplyOpen] = useState(false);
  const [sent, setSent] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const otherRefs = useRef<Record<number, HTMLInputElement | null>>({});

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

  /** Why question qi's typed value cannot go out yet (null: fine, or nothing typed). */
  const problemOf = (qi: number): AmKey | null => {
    const input = inputs[qi];
    if (!input || !(picked[qi] ?? []).includes(OTHER)) return null;
    const txt = (otherText[qi] ?? "").trim();
    if (!txt) return null; // unanswered: the every-question rule below already holds the card
    const r = checkTyped(input.kind, txt);
    return r.ok ? null : r.reason;
  };

  const replyText = reply.trim().slice(0, MAX_FREE_TEXT);
  const allAnswered = question.questions.every((_, qi) => answerOf(qi).length > 0);
  const canSubmit = !sent && (replyText.length > 0 || allAnswered);

  const submit = () => {
    if (!canSubmit) return;
    const bad = question.questions.findIndex((_, qi) => problemOf(qi) !== null);
    if (bad >= 0) {
      setTried(true);
      otherRefs.current[bad]?.focus();
      return;
    }
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

  // Number keys act on the first unanswered question; "Something else" is
  // the last number. Typing in a field is never a shortcut.
  const firstOpen = question.questions.findIndex((_, i) => answerOf(i).length === 0);
  const activeQi = firstOpen === -1 ? question.questions.length - 1 : firstOpen;
  const many = question.questions.length > 1;

  // The card takes focus when it appears (the run is parked on it), unless
  // the person is drafting in a field.
  useEffect(() => {
    if (sent) return;
    const el = document.activeElement as HTMLElement | null;
    const field =
      el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
        ? el.value
        : el?.isContentEditable
          ? el.textContent ?? ""
          : null;
    if (field !== null && field.trim().length > 0) return;
    rootRef.current?.focus({ preventScroll: true });
    // Only when the card first appears for this prompt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question.promptId]);

  useEffect(() => {
    if (sent) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "Enter") {
        if (e.shiftKey) return;
        const root = rootRef.current;
        const node = el instanceof Node ? el : null;
        const inside = !!(node && root && root.contains(node));
        const fromNowhere = !node || node === document.body || node === document.documentElement;
        if (!inside && !fromNowhere) return;
        if (inside && el instanceof HTMLButtonElement) {
          const kind = el.getAttribute("data-testid");
          if (kind === "question-skip" || kind === "question-submit" || kind === "question-reply-toggle") return;
          if (el.getAttribute("aria-checked") === "false") return;
        }
        if (!canSubmit) return;
        e.preventDefault();
        submit();
        return;
      }
      if (!/^[1-9]$/.test(e.key)) return;
      const d = Number(e.key);
      const q = question.questions[activeQi];
      if (!q) return;
      if (d <= q.options.length) {
        toggle(activeQi, q.options[d - 1]!.label, q.multiSelect);
        e.preventDefault();
      } else if (d === q.options.length + 1) {
        toggle(activeQi, OTHER, q.multiSelect);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sent, picked, otherText, question, reply, replyOpen]);

  const enterSubmits = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      title={t("question.keyHint")}
      className="my-2 rounded-xl border border-border bg-bg-surface px-4 py-3 shadow-lg focus:outline-none"
      data-testid="question-card"
      data-typed="true"
      data-prompt-id={question.promptId}
      data-active-question={activeQi}
    >
      <div className="flex items-center gap-2">
        <MessageCircleQuestion className="h-5 w-5 shrink-0 text-text-secondary" />
        <div className="text-sm font-semibold text-text-primary">{t("question.title")}</div>
        {many ? (
          <span className="rounded bg-bg-base px-1.5 py-0.5 text-[11px] tabular-nums text-text-secondary" data-testid="question-count">
            {t("question.count", { n: String(question.questions.length) })}
          </span>
        ) : null}
      </div>

      {question.questions.map((q, qi) => {
        const cur = picked[qi] ?? [];
        const otherOn = cur.includes(OTHER);
        const back = many && qi !== activeQi && !sent;
        const input = inputs[qi] ?? null;
        const problem = problemOf(qi);
        return (
          <fieldset key={qi} className="mt-3" data-testid="question-item" data-active={many && qi === activeQi ? "true" : undefined} disabled={sent}>
            <legend className={cls("flex flex-wrap items-center gap-2 text-sm", back ? "text-text-secondary" : "text-text-primary")}>
              <span className="rounded bg-bg-base px-1.5 py-0.5 text-[11px] font-medium text-text-secondary" data-testid="question-header">
                {q.header}
              </span>
              <span className="font-medium">{q.question}</span>
              {many && qi === activeQi && !sent ? (
                <span className="rounded bg-bg-base px-1.5 py-0.5 text-[11px] text-text-secondary" data-testid="question-active-marker">
                  {t("question.activeMarker", { n: String(qi + 1), total: String(question.questions.length) })}
                </span>
              ) : null}
            </legend>
            {q.multiSelect ? <div className="mt-0.5 text-xs text-text-secondary">{t("question.multiHint")}</div> : null}
            <div className="mt-1.5 flex flex-col gap-1">
              {q.options.map((o, oi) => {
                const on = cur.includes(o.label);
                return (
                  <button
                    key={o.label}
                    type="button"
                    role={q.multiSelect ? "checkbox" : "radio"}
                    aria-checked={on}
                    data-testid="question-option"
                    onClick={() => toggle(qi, o.label, q.multiSelect)}
                    className={cls(
                      "focus-ring flex w-full items-baseline gap-2 rounded-lg border px-3 py-1.5 text-left text-sm",
                      on ? "border-accent bg-accent/10 text-text-primary" : "border-transparent bg-bg-base text-text-primary hover:bg-bg-elevated",
                    )}
                  >
                    <span className="shrink-0 text-xs tabular-nums text-text-secondary" aria-hidden data-testid="question-option-number">
                      {oi + 1}
                    </span>
                    <span className="flex min-w-0 flex-col">
                      <span className="font-medium">{o.label}</span>
                      {o.description ? <span className="text-xs text-text-secondary">{o.description}</span> : null}
                    </span>
                  </button>
                );
              })}
              <button
                type="button"
                role={q.multiSelect ? "checkbox" : "radio"}
                aria-checked={otherOn}
                data-testid="question-other"
                onClick={() => toggle(qi, OTHER, q.multiSelect)}
                className={cls(
                  "focus-ring flex w-full items-baseline gap-2 rounded-lg border border-dashed px-3 py-1.5 text-left text-sm",
                  otherOn ? "border-accent bg-accent/10 text-text-primary" : "border-border-subtle bg-bg-base text-text-secondary hover:bg-bg-elevated",
                )}
              >
                <span className="shrink-0 text-xs tabular-nums text-text-secondary" aria-hidden>
                  {q.options.length + 1}
                </span>
                <span className="font-medium">{t("question.other")}</span>
              </button>
              {otherOn ? (
                input ? (
                  <TypedOther
                    id={`${question.promptId}-${qi}`}
                    input={input}
                    value={otherText[qi] ?? ""}
                    problem={tried || left[qi] ? problem : null}
                    inputRef={(el) => {
                      otherRefs.current[qi] = el;
                    }}
                    onChange={(v) => setOtherText((p) => ({ ...p, [qi]: v }))}
                    onBlur={() => setLeft((p) => (p[qi] ? p : { ...p, [qi]: true }))}
                    onKeyDown={enterSubmits}
                  />
                ) : (
                  <input
                    type="text"
                    autoFocus
                    maxLength={MAX_FREE_TEXT}
                    value={otherText[qi] ?? ""}
                    onChange={(e) => setOtherText((p) => ({ ...p, [qi]: e.target.value }))}
                    onKeyDown={enterSubmits}
                    placeholder={t("question.otherPlaceholder")}
                    data-testid="question-other-text"
                    className="focus-ring w-full rounded-md border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary"
                  />
                )
              ) : null}
            </div>
          </fieldset>
        );
      })}

      <div className="mt-3">
        {!replyOpen ? (
          <button
            type="button"
            disabled={sent}
            onClick={() => setReplyOpen(true)}
            className="focus-ring text-xs text-text-secondary underline-offset-2 hover:text-text-primary hover:underline disabled:opacity-50"
            data-testid="question-reply-toggle"
          >
            {t("question.replyToggle")}
          </button>
        ) : (
          <>
            <label className="text-xs text-text-secondary" htmlFor={`question-reply-${question.promptId}`}>
              {t("question.replyLabel")}
            </label>
            <textarea
              id={`question-reply-${question.promptId}`}
              rows={1}
              autoFocus
              maxLength={MAX_FREE_TEXT}
              value={reply}
              disabled={sent}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={enterSubmits}
              placeholder={t("question.replyPlaceholder")}
              data-testid="question-reply"
              className="focus-ring mt-1 w-full resize-none rounded-md border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary"
            />
          </>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto hidden text-[11px] text-text-secondary sm:inline">{t("question.keyHint")}</span>
        <button
          type="button"
          onClick={skip}
          title={t("question.skipTitle")}
          disabled={sent}
          className="focus-ring rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-bg-elevated disabled:opacity-50"
          data-testid="question-skip"
        >
          {t("question.skip")}
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="focus-ring rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-bg-base hover:opacity-90 disabled:opacity-50"
          data-testid="question-submit"
        >
          {t("question.submit")}
        </button>
      </div>
    </div>
  );
}

/** The "Something else" box of a question that takes a typed value: its
 *  hint as placeholder and label, and the reason when the value is not one. */
function TypedOther({
  id,
  input,
  value,
  problem,
  inputRef,
  onChange,
  onBlur,
  onKeyDown,
}: {
  id: string;
  input: { kind: InputKind; hint: string };
  value: string;
  problem: AmKey | null;
  inputRef: (el: HTMLInputElement | null) => void;
  onChange: (v: string) => void;
  onBlur: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
}) {
  const t = useAmT();
  const hint = input.hint || t(`typed.hint.${input.kind}` as AmKey);
  const errId = `am-typed-err-${id}`;
  return (
    <div className="flex flex-col gap-1">
      <input
        ref={inputRef}
        type="text"
        autoFocus
        maxLength={MAX_FREE_TEXT}
        value={value}
        inputMode={input.kind === "money" || input.kind === "rate" ? "decimal" : input.kind === "tax_id" ? "numeric" : undefined}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        placeholder={hint}
        aria-label={hint}
        aria-invalid={!!problem}
        aria-describedby={problem ? errId : undefined}
        data-testid="question-other-text"
        data-kind={input.kind}
        className={cls(
          "focus-ring w-full rounded-md border bg-bg-base px-3 py-1.5 text-sm text-text-primary",
          problem ? "border-status-danger" : "border-border",
        )}
      />
      {problem ? (
        <p id={errId} role="alert" className="text-xs text-status-danger" data-testid="am-typed-error">
          {t(problem)}
        </p>
      ) : null}
    </div>
  );
}
