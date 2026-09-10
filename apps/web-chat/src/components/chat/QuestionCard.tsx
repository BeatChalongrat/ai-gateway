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
//
// FIX77 Part D — appearance only, the FIX73/74 contract is untouched:
//   * the model-written header chip renders as written (no `uppercase` —
//     it does nothing to Thai and mangles a mixed Thai/English header);
//   * options are numbered and the number keys choose them (multi-select
//     toggles); typing in a field never triggers a shortcut;
//   * the free reply gets a distinct job ("answers everything above") and
//     hides behind a toggle so it no longer competes with "Something else"
//     (whose job is: my own answer to THIS question).

import { MessageCircleQuestion } from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
  const [replyOpen, setReplyOpen] = useState(false);
  const [sent, setSent] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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

  // FIX77 D4 — number keys pick options in the ACTIVE question: the first
  // unanswered one (with one question, simply that question). "Something
  // else" is the last number. Never fires while the user types in a field.
  // FIX88 Part C — the active question is shown, not just computed (the
  // marker on its legend), and Enter confirms once an answer exists.
  const firstOpen = question.questions.findIndex((_, i) => answerOf(i).length === 0);
  const activeQi = firstOpen === -1 ? question.questions.length - 1 : firstOpen;
  const many = question.questions.length > 1;

  // FIX88 Part C — the card takes focus when it appears, so the keys that
  // answer it reach it. Measured on the real bundle: with Part B returning
  // the caret to the composer after a send, the number keys landed in the
  // composer as text (FIX77 D4's correct rule: typing in a field is never a
  // shortcut) and Enter sent a message instead of answering. The turn is
  // PARKED on this card — it is the current task, not an interruption.
  // The exception is a person actually drafting: a composer (or any field)
  // with text in it keeps the caret, and the card is still reachable by
  // Tab, by mouse, and through the thread's own "question" pill.
  // The card root is not a text field, so no soft keyboard opens; scroll is
  // left to the thread (FIX30 brings the card into view or raises the pill).
  useEffect(() => {
    if (sent) return;
    const el = document.activeElement as HTMLElement | null;
    const field =
      el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
        ? el.value
        : el?.isContentEditable
          ? el.textContent ?? ""
          : null;
    if (field !== null && field.trim().length > 0) return; // a real draft: leave it
    rootRef.current?.focus({ preventScroll: true });
    // Only when the card first appears for this prompt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question.promptId]);

  useEffect(() => {
    if (sent) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const el = e.target as HTMLElement | null;
      // The two text inputs own their Enter (they submit from inside);
      // typing anywhere else (the composer) is never a shortcut.
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "Enter") {
        // FIX88 Part C — Enter confirms. The owner's screenshot: option 2
        // highlighted, Enter did nothing — worse, on a focused option
        // button Enter re-fired its click and DEselected it. Rules:
        //   * from nowhere (body — the number-key case) or from inside the
        //     card: confirm when an answer exists;
        //   * on a focused option that is NOT yet chosen: let the native
        //     click choose it (Tab + Enter still selects);
        //   * on Skip / Answer / the reply toggle: their own click;
        //   * on any control outside the card (Send, a menu): not ours.
        if (e.shiftKey) return;
        const root = rootRef.current;
        // e.target is the window itself for a key pressed with nothing
        // focused (jsdom and real browsers alike) — not a Node.
        const node = el instanceof Node ? el : null;
        const inside = !!(node && root && root.contains(node));
        const fromNowhere = !node || node === document.body || node === document.documentElement;
        if (!inside && !fromNowhere) return;
        if (inside && el instanceof HTMLButtonElement) {
          const kind = el.getAttribute("data-testid");
          if (kind === "question-skip" || kind === "question-submit" || kind === "question-reply-toggle") return;
          if (el.getAttribute("aria-checked") === "false") return; // choose it first
        }
        if (!canSubmit) return;
        e.preventDefault();
        submit();
        return;
      }
      if (!/^[1-9]$/.test(e.key)) return;
      const d = Number(e.key);
      const qi = activeQi;
      const q = question.questions[qi];
      if (!q) return;
      if (d <= q.options.length) {
        toggle(qi, q.options[d - 1]!.label, q.multiSelect);
        e.preventDefault();
      } else if (d === q.options.length + 1) {
        toggle(qi, OTHER, q.multiSelect);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sent, picked, otherText, question, reply, replyOpen]);

  return (
    <div
      ref={rootRef}
      // Focusable as a container (never in the tab order): the card takes
      // focus when it appears so 1–9 and Enter reach it, and Enter here is
      // the card's own — the option buttons keep theirs.
      tabIndex={-1}
      // FIX96 — the keyboard legend is hidden below `sm` (a phone has no
      // number row to press), so the card itself carries it as a title:
      // the shortcut is never undiscoverable, only unobtrusive.
      title={t("question.keyHint")}
      className="mx-auto my-2 max-w-xl rounded-xl border border-border bg-bg-surface px-4 py-3 shadow-lg focus:outline-none"
      data-testid="question-card"
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
        // FIX96 — the question the number keys act on stands forward and the
        // rest step back. The step is a TOKEN, not opacity: measured on this
        // card's surface, the largest opacity that still keeps the quietest
        // text on it at 4.5:1 is 0.91 (dark) / 0.88 (light) — a dim nobody
        // can see. text-secondary is 5.12:1 dark and 5.96:1 light against
        // bg-surface, so the hierarchy is visible AND every question stays
        // readable. Nothing about which question is active changes here;
        // this is the same activeQi the keys already used.
        const back = many && qi !== activeQi && !sent;
        return (
          <fieldset key={qi} className="mt-3" data-testid="question-item" data-active={many && qi === activeQi ? "true" : undefined} disabled={sent}>
            <legend className={`flex flex-wrap items-center gap-2 text-sm ${back ? "text-text-secondary" : "text-text-primary"}`}>
              {/* the header the MODEL wrote, as written — uppercasing does
                  nothing to Thai and breaks a mixed-script chip (FIX77 D1) */}
              <span className="rounded bg-bg-base px-1.5 py-0.5 text-[11px] font-medium text-text-secondary" data-testid="question-header">
                {q.header}
              </span>
              <span className="font-medium">{q.question}</span>
              {/* FIX88 Part C — which question the number keys act on was
                  invisible state. FIX96 keeps the fact and drops the blue
                  pill: the active question's own text stays at full strength
                  while the others step back to secondary (`back` above), and
                  the marker rides along as a neutral chip. It was tried
                  WITHOUT the marker first — the step-back does say which
                  question is current, but every question numbers its options
                  from 1, so with four lists on screen nothing said which "1"
                  the key would hit. The marker answers that; it stays. */}
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
                    className={`focus-ring flex w-full items-baseline gap-2 rounded-lg border px-3 py-1.5 text-left text-sm ${on ? "border-accent bg-accent/10 text-text-primary" : "border-transparent bg-bg-base text-text-primary hover:bg-bg-elevated"}`}
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
                className={`focus-ring flex w-full items-baseline gap-2 rounded-lg border border-dashed px-3 py-1.5 text-left text-sm ${otherOn ? "border-accent bg-accent/10 text-text-primary" : "border-border-subtle bg-bg-base text-text-secondary hover:bg-bg-elevated"}`}
              >
                <span className="shrink-0 text-xs tabular-nums text-text-secondary" aria-hidden>
                  {q.options.length + 1}
                </span>
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

      {/* FIX77 D3 — the free reply answers EVERYTHING above (as opposed to
          "Something else", which answers one question); it stays behind a
          toggle so two empty text boxes never compete for the same answer. */}
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
          </>
        )}
      </div>

      {/* FIX96 — the keyboard legend moved here from above the first
          question, where it competed with the title for the top line. It is
          still visible (on anything wider than a phone) and still carried by
          the card's own title attribute, so a pointer user finds it too. */}
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
