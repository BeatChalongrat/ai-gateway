// Agent Market cards ↔ the app's QuestionCard.
//
// The workspace reuses @/components/chat/QuestionCard as is. It speaks
// PendingQuestion in and QuestionAnswerPayload out, keyed by question TEXT;
// the control plane speaks questions keyed by `key` and answers of
// {labels:[…]} (clicked options) or {text} (the person's own words).

import type { PendingQuestion, QuestionAnswerPayload } from "@/lib/types";

import type { AnswerBody, CardPayload, Question, Submitted } from "./types";

/** Card payload → the QuestionCard's input. */
export function toPendingQuestion(card: Pick<CardPayload, "card_id" | "questions">): PendingQuestion {
  return {
    promptId: card.card_id,
    questions: (card.questions ?? []).map((q) => ({
      question: q.question,
      header: q.header,
      multiSelect: Boolean(q.multiSelect),
      options: (q.options ?? []).map((o) => ({ label: o.label, description: o.description ?? "" })),
    })),
  };
}

function sameJSON(a: unknown, b: unknown): boolean {
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

/** The option labels that make up a question's recommended default. */
export function defaultLabels(q: Question): string[] {
  const opts = q.options ?? [];
  if (Array.isArray(q.default)) {
    const wanted = q.default as unknown[];
    const hits = opts.filter((o) => wanted.some((w) => sameJSON(w, o.value))).map((o) => o.label);
    if (hits.length) return hits;
  } else if (q.default !== undefined) {
    const hit = opts.find((o) => sameJSON(o.value, q.default));
    if (hit) return [hit.label];
  }
  if (q.default_label && opts.some((o) => o.label === q.default_label)) return [q.default_label];
  return [];
}

/** QuestionCard's answer → the body of POST /cards/{id}/answer.
 *
 *  - labels that are all options  → {labels}
 *  - any label that is not an option (the card's "Something else" box)
 *    → {text} with the person's words
 *  - the free reply (`response`)  → body `text`
 *  - Skip ("let the agent decide") → each question's recommended default,
 *    marked `skipped` so the run records it as the agent's choice. */
export function toAnswerBody(questions: Question[], answer: QuestionAnswerPayload): AnswerBody {
  const answers: Record<string, Submitted> = {};
  if (answer.outcome === "skipped") {
    for (const q of questions) {
      const labels = defaultLabels(q);
      if (labels.length) answers[q.key] = { labels, skipped: true };
    }
    return { answers };
  }
  for (const q of questions) {
    const picked = (answer.answers[q.question] ?? []).map((l) => l.trim()).filter(Boolean);
    if (picked.length === 0) continue;
    const options = new Set((q.options ?? []).map((o) => o.label));
    answers[q.key] = picked.every((l) => options.has(l)) ? { labels: picked } : { text: picked.join(", ") };
  }
  const body: AnswerBody = { answers };
  const reply = answer.response?.trim();
  if (reply) body.text = reply;
  return body;
}

/** An option whose value is the string "wait" (request_files: "I will upload"). */
export function isWaitOption(value: unknown): boolean {
  return value === "wait";
}
