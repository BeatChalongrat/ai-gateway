"use client";

// An agent's question card IS the app's QuestionCard (AM5) — the same
// markup, test ids, number keys, Enter, Skip and free reply, the same
// QuestionAnswerPayload out, so toAnswerBody() is unchanged. One thing is
// this surface's own, and it is passed in rather than copied:
//   * a question that takes a typed value (`input: {kind, hint}`, the
//     invoice agent's E2 cards: an amount, a tax ID, a date, a rate, text)
//     brings its own "Something else" field: the hint as placeholder and
//     label, and the check of typed.ts — an invalid value gets an inline
//     reason and the card does not send until it is fixed (the server checks
//     again).
// The card reads in the app's language, like the workspace around it (AM11;
// AM4 had it Thai whatever the app's language). What the hint shows as an
// example follows the language too — 15/09/2569 in Thai, 15/09/2026 in
// English — and typed.ts reads either, in either language.

import { useMemo } from "react";

import { MAX_FREE_TEXT, QuestionCard, type OtherField, type OtherFieldProps } from "@/components/chat/QuestionCard";
import type { PendingQuestion, QuestionAnswerPayload } from "@/lib/types";

import { tAm, useAmT, type AmKey } from "../i18n";
import { checkTyped } from "../typed";
import type { InputKind } from "../types";
import { cls, Thai } from "../ui";

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
  const sig = inputs.map((i) => (i ? `${i.kind}:${i.hint}` : "")).join("|");
  const others = useMemo<Array<OtherField | null>>(
    () =>
      inputs.map((input) =>
        input
          ? {
              check: (text) => {
                const r = checkTyped(input.kind, text);
                return r.ok ? null : tAm(r.reason);
              },
              render: (p) => <TypedOther input={input} {...p} />,
            }
          : null,
      ),
    // the inputs' content is what matters, not the array's identity
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sig],
  );
  return (
    <Thai>
      <QuestionCard question={question} others={others} onAnswer={onAnswer} />
    </Thai>
  );
}

/** The "Something else" box of a question that takes a typed value: its
 *  hint as placeholder and label, and the reason when the value is not one. */
function TypedOther({ id, input, value, problem, inputRef, onChange, onBlur, onKeyDown }: OtherFieldProps & { input: { kind: InputKind; hint: string } }) {
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
          {problem}
        </p>
      ) : null}
    </div>
  );
}
