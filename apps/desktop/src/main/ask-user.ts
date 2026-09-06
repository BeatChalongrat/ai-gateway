// FIX73 — `ask_user_question`: the one tool that pauses a turn to ask the
// USER something, the way the reference design's AskUserQuestion does. The contract:
//
//   • it is a TOOL the model calls (1–4 questions, 2–4 options each, an
//     optional multi-select), not a UI trick — so the model decides when to
//     ask, and the prompt (prompt-assembler.ts) tells it to ask rarely;
//   • the card rides the desktop bridge as its OWN message pair
//     (question.request / question.respond, bridge-protocol.ts) — never
//     consent.request, whose payload means "approve this tool call";
//   • the answer returns as the TOOL RESULT of the same call: one line per
//     question with the chosen label(s), or the user's own words when they
//     typed instead of choosing ("The user responded: …"), or a plain
//     statement that they declined (Skip). The word "Other" never reaches the
//     model — the client adds that row and returns the user's text;
//   • a stored permission can never answer it (it never enters the
//     permission path), a sub-agent or scheduled task never receives it, and
//     nobody answers on the user's behalf.
//
// The reference design documents only `header ≤ 12` and the two counts; every other
// limit here is OURS, with the reason beside it. No 'electron' import: the
// tests load this in plain Node.

import type { GatewayToolDefinition } from "./gateway-client";

export const ASK_USER_TOOL = "ask_user_question";

/** Limits. From the reference design: MAX_QUESTIONS, MIN/MAX_OPTIONS, MAX_HEADER. Ours: the
 *  three text lengths — a card must be readable at a glance (question), the
 *  label is the exact string that returns as the answer (one row), and a
 *  description is at most two lines under it. */
export const ASK_LIMITS = Object.freeze({
  MAX_QUESTIONS: 4,
  MIN_OPTIONS: 2,
  MAX_OPTIONS: 4,
  MAX_HEADER: 12,
  MAX_QUESTION: 500,
  MAX_LABEL: 80,
  MAX_DESCRIPTION: 200,
  /** The free-text reply the card accepts (either per "Something else" row or
   *  instead of answering). Bounded like a normal user message would be. */
  MAX_FREE_TEXT: 2000,
});

export interface AskOption {
  readonly label: string;
  readonly description: string;
}
export interface AskQuestion {
  readonly question: string;
  readonly header: string;
  readonly options: readonly AskOption[];
  readonly multiSelect: boolean;
}

/** A label that means "none of these" — the CLIENT owns that row (Part 2c);
 *  a model-emitted one is a choice with no meaning and is refused. */
const OTHER_LABEL_RE = /^\s*(other|others|something else|none of (the|these) above|อื่น\s*ๆ|อื่นๆ|อื่น|ไม่ใช่ข้อไหนเลย)\s*[.:…]?\s*$/i;

export const ASK_USER_TOOL_DEFINITION: GatewayToolDefinition = {
  type: "function",
  function: {
    name: ASK_USER_TOOL,
    description:
      "Ask the user one to four multiple-choice questions and wait for the answer. The turn pauses until they reply; " +
      "the reply comes back as this tool's result. Use it ONLY for a matter of the user's preference or intent that you " +
      "cannot determine yourself — scope, audience, format, destination — and never for anything you could look up, " +
      "read from the conversation, or reasonably assume. Do not add an 'Other' option: the app adds one that accepts free text.",
    parameters: {
      type: "object",
      properties: {
        questions: {
          type: "array",
          minItems: 1,
          maxItems: ASK_LIMITS.MAX_QUESTIONS,
          description: "1–4 questions. Ask as few as possible; one is usually right.",
          items: {
            type: "object",
            properties: {
              question: { type: "string", maxLength: ASK_LIMITS.MAX_QUESTION, description: "The full question, in the user's language." },
              header: { type: "string", maxLength: ASK_LIMITS.MAX_HEADER, description: "A short chip label for the question (max 12 characters), e.g. 'Format'." },
              options: {
                type: "array",
                minItems: ASK_LIMITS.MIN_OPTIONS,
                maxItems: ASK_LIMITS.MAX_OPTIONS,
                description: "2–4 distinct choices. Never include 'Other' or 'Something else'.",
                items: {
                  type: "object",
                  properties: {
                    label: { type: "string", maxLength: ASK_LIMITS.MAX_LABEL, description: "The choice as the user will read it; returned verbatim when picked." },
                    description: { type: "string", maxLength: ASK_LIMITS.MAX_DESCRIPTION, description: "Optional one-line explanation of the choice." },
                  },
                  required: ["label"],
                },
              },
              multiSelect: { type: "boolean", description: "true when the user may pick more than one option. Default false." },
            },
            required: ["question", "header", "options"],
          },
        },
      },
      required: ["questions"],
    },
  },
};

export type AskValidation = { readonly ok: true; readonly questions: readonly AskQuestion[] } | { readonly ok: false; readonly reason: string };

/** Validate the model's arguments against the contract. Every failure is a
 *  tool ERROR back to the model (it can re-ask correctly); the user never
 *  sees a broken card. */
export function validateAskArgs(args: Record<string, unknown>): AskValidation {
  const raw = args["questions"];
  if (!Array.isArray(raw)) return { ok: false, reason: "'questions' must be an array" };
  if (raw.length < 1) return { ok: false, reason: "'questions' must hold at least one question" };
  if (raw.length > ASK_LIMITS.MAX_QUESTIONS) return { ok: false, reason: `at most ${ASK_LIMITS.MAX_QUESTIONS} questions per call (got ${raw.length})` };
  const questions: AskQuestion[] = [];
  const headers = new Set<string>();
  for (let i = 0; i < raw.length; i++) {
    const q = raw[i];
    const where = `questions[${i}]`;
    if (typeof q !== "object" || q === null || Array.isArray(q)) return { ok: false, reason: `${where} must be an object` };
    const o = q as Record<string, unknown>;
    const question = typeof o["question"] === "string" ? o["question"].trim() : "";
    if (!question) return { ok: false, reason: `${where}.question is required` };
    if (question.length > ASK_LIMITS.MAX_QUESTION) return { ok: false, reason: `${where}.question is longer than ${ASK_LIMITS.MAX_QUESTION} characters — put the explanation in your message, keep the question short` };
    const header = typeof o["header"] === "string" ? o["header"].trim() : "";
    if (!header) return { ok: false, reason: `${where}.header is required (a label of at most ${ASK_LIMITS.MAX_HEADER} characters)` };
    if ([...header].length > ASK_LIMITS.MAX_HEADER) return { ok: false, reason: `${where}.header is longer than ${ASK_LIMITS.MAX_HEADER} characters` };
    const hk = header.toLowerCase();
    if (headers.has(hk)) return { ok: false, reason: `${where}.header '${header}' repeats an earlier question's header` };
    headers.add(hk);
    const opts = o["options"];
    if (!Array.isArray(opts)) return { ok: false, reason: `${where}.options must be an array` };
    if (opts.length < ASK_LIMITS.MIN_OPTIONS || opts.length > ASK_LIMITS.MAX_OPTIONS) {
      return { ok: false, reason: `${where}.options must hold ${ASK_LIMITS.MIN_OPTIONS}–${ASK_LIMITS.MAX_OPTIONS} choices (got ${opts.length})` };
    }
    const options: AskOption[] = [];
    const labels = new Set<string>();
    for (let j = 0; j < opts.length; j++) {
      const op = opts[j];
      const ow = `${where}.options[${j}]`;
      if (typeof op !== "object" || op === null || Array.isArray(op)) return { ok: false, reason: `${ow} must be an object` };
      const oo = op as Record<string, unknown>;
      const label = typeof oo["label"] === "string" ? oo["label"].trim() : "";
      if (!label) return { ok: false, reason: `${ow}.label is required` };
      if (label.length > ASK_LIMITS.MAX_LABEL) return { ok: false, reason: `${ow}.label is longer than ${ASK_LIMITS.MAX_LABEL} characters` };
      if (OTHER_LABEL_RE.test(label)) return { ok: false, reason: `${ow}.label '${label}' — do not offer an 'Other' choice; the app adds one that accepts the user's own text` };
      const lk = label.toLowerCase();
      if (labels.has(lk)) return { ok: false, reason: `${ow}.label '${label}' repeats an earlier option` };
      labels.add(lk);
      const description = typeof oo["description"] === "string" ? oo["description"].trim() : "";
      if (description.length > ASK_LIMITS.MAX_DESCRIPTION) return { ok: false, reason: `${ow}.description is longer than ${ASK_LIMITS.MAX_DESCRIPTION} characters` };
      options.push({ label, description });
    }
    questions.push({ question, header, options, multiSelect: o["multiSelect"] === true });
  }
  return { ok: true, questions };
}

/** What the page posts back (question.respond). `answers` is keyed by the
 *  question's text (the reference shape); each value is the chosen label(s) or
 *  the user's own text for the "Something else" row. `response` is a free
 *  reply typed INSTEAD of answering. `skipped` is the decline. */
export interface QuestionAnswer {
  readonly promptId: string;
  readonly outcome: "answered" | "skipped";
  readonly answers: Readonly<Record<string, readonly string[]>>;
  readonly response: string | null;
}

/** The tool result the model reads. Stable wording — the prompt and the
 *  tests refer to it. */
export function formatAnswerForModel(questions: readonly AskQuestion[], a: QuestionAnswer): string {
  if (a.outcome === "skipped") {
    return "The user chose not to answer. Proceed on your own judgment, say what you assumed, and do not ask the same question again this turn.";
  }
  const response = (a.response ?? "").trim();
  if (response) return `The user responded: ${JSON.stringify(response)}`;
  const lines: string[] = [];
  for (const q of questions) {
    const picked = (a.answers[q.question] ?? []).map((s) => s.trim()).filter(Boolean);
    lines.push(`${JSON.stringify(q.question)}: ${picked.length ? picked.join(", ") : "(no answer)"}`);
  }
  return lines.join("\n");
}

export const NOT_AVAILABLE_IN_THIS_RUN = `Error: ${ASK_USER_TOOL} is not available in this run (a sub-agent or a scheduled task cannot ask the user). Make the most reasonable assumption and state it in your answer.`;
export const CANCELLED_WHILE_ASKING = "Not executed: the turn was cancelled.";
