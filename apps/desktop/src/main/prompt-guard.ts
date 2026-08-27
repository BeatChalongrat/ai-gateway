// FIX22 §1a/§1b — the host's write-time prompt guard and 403 explainer.
//
// §1a (write-time refusal): text the host is about to STORE into a prompt
// source — a `remember` note bound for the sidecar's local store, or the
// writing-style profile bound for the sidecar's settings — is checked FIRST
// against the gateway's content guardrail (POST /v1/guardrails/check, the
// chat path's own checker). A stored string that matches a rule 403s every
// later turn, silently and permanently (FIX21 §5). The sidecar holds no
// gateway key (FIX3), so the host — which does — is the choke point: the
// sidecar never sees a refused write, and its own code is unchanged.
//
// If the gateway cannot answer (older gateway without the route, network
// down), the shared mirror decides instead — that is the ONE place the
// mirror is allowed to refuse, and §1b's constraint 1 names it explicitly
// ("at write time per §1a"). The verdict says which side decided.
//
// §1b (legibility): after the gateway returns a guardrail 403, explain it:
// which labelled part of the assembled prompt carries a matching span and
// where to remove it. DIAGNOSTIC ONLY — it runs after the refusal, appends
// text to the error the page already shows, and cannot block a turn. The
// explanation is "likely": the backend joins all user+system text before
// matching, so its first-hit rule can differ from the per-part first hit
// here; when nothing matches the message says so instead of guessing.
//
// The matched span is shown to the user and never written into a note, a
// title, a retry, or a message that gets re-sent.

import { explainGuardrailBlock, findGuardrailMatch, type PromptPart, type PromptPartMatch } from "../shared/guardrail-mirror";
import type { Logger } from "./logger";

/** The gateway's verdict (gateway-client.guardrailCheck); null = no answer. */
export interface GuardrailVerdict {
  readonly passed: boolean;
  readonly ruleName: string | null;
  readonly matched: string | null;
}

export interface GuardrailChecker {
  guardrailCheck(text: string, signal: AbortSignal): Promise<GuardrailVerdict | null>;
}

export type WriteVerdict =
  | { readonly blocked: false }
  | { readonly blocked: true; readonly rule: string; readonly span: string | null; readonly decidedBy: "gateway" | "mirror" };

const CHECK_TIMEOUT_MS = 8_000;

export class PromptGuard {
  constructor(
    private readonly gateway: GuardrailChecker,
    private readonly log: Logger,
  ) {}

  /** §1a — would storing `text` poison every future turn? Gateway first;
   * mirror only when the gateway gives no answer. Never throws. */
  async checkForWrite(text: string, what: string): Promise<WriteVerdict> {
    const trimmed = text.trim();
    if (!trimmed) return { blocked: false };
    let verdict: GuardrailVerdict | null = null;
    try {
      verdict = await this.gateway.guardrailCheck(trimmed, AbortSignal.timeout(CHECK_TIMEOUT_MS));
    } catch (err) {
      this.log.warn(`PROMPT GUARD: gateway check threw for ${what}: ${(err as Error).message}`);
      verdict = null;
    }
    if (verdict) {
      if (verdict.passed) return { blocked: false };
      this.log.warn(`PROMPT GUARD: refused ${what} — gateway rule ${verdict.ruleName ?? "?"}`);
      return { blocked: true, rule: verdict.ruleName ?? "unknown", span: verdict.matched, decidedBy: "gateway" };
    }
    // No gateway answer → the shared mirror (the one write-time use §1b allows).
    const m = findGuardrailMatch(trimmed);
    if (!m) {
      this.log.info(`PROMPT GUARD: gateway unavailable; mirror passed ${what}`);
      return { blocked: false };
    }
    this.log.warn(`PROMPT GUARD: gateway unavailable; refused ${what} by the mirror — rule ${m.rule}`);
    return { blocked: true, rule: m.rule, span: m.span, decidedBy: "mirror" };
  }

  /** The user/model-facing refusal. Starts with "Error:" so a tool result
   * built from it is unmistakably a failure (the FIX5 lie). */
  static refusalMessage(v: Extract<WriteVerdict, { blocked: true }>, what: string): string {
    const phrase = v.span ? `the phrase ${JSON.stringify(v.span)}` : "wording";
    const decided = v.decidedBy === "gateway" ? "the gateway's content guardrail" : "the gateway's content guardrail (checked locally — the gateway did not answer)";
    return (
      `Error: not saved — ${what} contains ${phrase}, which ${decided} blocks (rule ${v.rule}). ` +
      `If it were saved, every future conversation would be refused. Reword that phrase and try again.`
    );
  }

  /** §1b — explain a guardrail 403 the gateway already returned. Pure; never
   * blocks; returns text to APPEND to the existing error message. */
  static explain403(parts: readonly PromptPart[]): string {
    const hits = explainGuardrailBlock(parts);
    if (hits.length === 0) {
      return (
        "\n\nWhy: the gateway's content guardrail matched something in this request, but this app could not find the phrase " +
        "(the gateway's rules may be newer than this app's). Check for jailbreak-like wording in your message, then in " +
        "Memory (including notes saved on this computer), the project's instructions and Writing style — and remove it."
      );
    }
    return "\n\nWhy (likely): " + hits.map(describeHit).join(" ");
  }
}

function describeHit(h: PromptPartMatch): string {
  return `${h.label} contains ${JSON.stringify(h.span)}, which matches guardrail rule ${h.rule} — remove it ${h.fixAt}.`;
}

/** Labels + fix locations for the prompt parts the host assembles. Kept here
 * so the coordinator and the tests spell them identically. */
export const PROMPT_PART = Object.freeze({
  userMessage: { label: "Your message", fixAt: "by rewording the message" },
  earlierMessage: { label: "An earlier message in this conversation", fixAt: "by starting a new conversation or editing that message" },
  projectInstructions: (name: string) => ({ label: `The instructions of project “${name}”`, fixAt: "in Projects → that project's instructions" }),
  writingStyle: { label: "Your writing-style profile", fixAt: "in Writing style" },
  serverMemory: { label: "A saved memory", fixAt: "in Memory (delete or edit that memory)" },
  localGlobalNote: { label: "A note saved on this computer (remember, global)", fixAt: "in Memory → Notes on this computer" },
  localConversationNote: { label: "A note saved for this conversation (remember)", fixAt: "by starting a new conversation" },
  // FIX27: the extracted text of an attached document (untrusted observation envelope).
  attachedFile: (names: string) => ({ label: `The attached file ${names}`, fixAt: "by removing that attachment and sending the message again" }),
} as const);
