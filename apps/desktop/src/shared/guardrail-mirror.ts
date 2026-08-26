// FIX22 §1b — the ONE client-side mirror of the gateway's content-guardrail
// rules, shared by the CI tripwire (test/prompt-guardrail-safe.test.ts) and
// the runtime diagnostic (main/prompt-guard.ts). One mirror, not two.
//
// SOURCE OF TRUTH: backend/app/services/guardrail_service.py RULES, compiled
// with re.IGNORECASE | re.DOTALL → JS flags "is". Copied verbatim. If a rule
// changes there, mirror it here; the positive control in the test fails
// loudly if these patterns stop matching a real trigger, so drift that
// neuters the mirror is itself caught.
//
// DIAGNOSTIC ONLY — NEVER ENFORCEMENT. The backend is the sole enforcer. This
// module explains a 403 the gateway already returned, or (at write time) is
// the FALLBACK when the gateway's own check endpoint cannot be reached. It
// must never block, refuse, or short-circuit a turn: a client-side security
// control is bypassable by definition and will drift. Nothing here imports
// 'electron' or touches I/O — it is a pure function over strings.

export type GuardrailRuleName = "jailbreak_dan" | "prompt_injection" | "harmful_synthesis";

export interface GuardrailPattern {
  readonly rule: GuardrailRuleName;
  readonly pattern: RegExp;
}

// backend/app/services/guardrail_service.py:21 RULES, re.IGNORECASE|re.DOTALL.
export const GUARDRAIL_PATTERNS: readonly GuardrailPattern[] = Object.freeze([
  // jailbreak_dan
  { rule: "jailbreak_dan", pattern: /you are now (?:DAN|an AI without restrictions|jailbroken)/is },
  { rule: "jailbreak_dan", pattern: /pretend you (?:have no|don't have) (?:rules|restrictions|guidelines|limits)/is },
  { rule: "jailbreak_dan", pattern: /act as (?:if you were trained|an unrestricted|a different AI)/is },
  { rule: "jailbreak_dan", pattern: /(?:ignore|disregard|forget)(?:\s+(?:all|your|previous|the))*\s+(?:instructions|rules|guidelines|restrictions|system\s+prompt)/is },
  { rule: "jailbreak_dan", pattern: /you (?:have no|must have no) (?:ethical )?(?:restrictions|limits|guidelines)/is },
  // prompt_injection
  { rule: "prompt_injection", pattern: /new (?:system )?(?:instruction|prompt|directive|command):/is },
  { rule: "prompt_injection", pattern: /<\|(?:system|endoftext|im_start|im_end)\|>/is },
  { rule: "prompt_injection", pattern: /\[SYSTEM\]|\[INST\]|\[\/INST\]/is },
  { rule: "prompt_injection", pattern: /###\s*(?:System|Instruction|Human|Assistant):/is },
  // harmful_synthesis
  { rule: "harmful_synthesis", pattern: /(?:how to|steps to|guide (?:me |us )?to|instructions? for)\b.{0,40}\b(?:make|build|create|synthesize|manufacture)\b.{0,40}\b(?:bomb|explosive|weapon|poison|nerve agent|bioweapon)/is },
  { rule: "harmful_synthesis", pattern: /(?:synthesis|synthesize|produce|manufacture)\b.{0,30}\b(?:methamphetamine|fentanyl|sarin|VX gas|ricin)/is },
]);

export interface GuardrailMatch {
  readonly rule: GuardrailRuleName;
  /** The first matched span, capped — shown to the user, never re-sent. */
  readonly span: string;
}

const MAX_SPAN_CHARS = 200;

/** First rule (in the backend's own order) whose pattern matches `text`, or
 * null. Mirrors GuardrailService.check's first-hit semantics. */
export function findGuardrailMatch(text: string): GuardrailMatch | null {
  for (const { rule, pattern } of GUARDRAIL_PATTERNS) {
    const m = text.match(pattern);
    if (m) return { rule, span: m[0].slice(0, MAX_SPAN_CHARS) };
  }
  return null;
}

/** A labelled piece of an assembled prompt — e.g. "your message", "a saved
 * memory", "the project instructions" — with a hint on where to fix it. */
export interface PromptPart {
  readonly label: string;
  readonly text: string;
  /** Where the user goes to remove it (a UI location, not a URL). */
  readonly fixAt: string;
}

export interface PromptPartMatch extends GuardrailMatch {
  readonly label: string;
  readonly fixAt: string;
}

/** Every part that matches, in the order given. Used to explain a 403 after
 * the gateway returned it: the backend joins ALL user+system text before
 * matching, so the first-hit rule server-side may differ from the client's
 * per-part first hit — callers must present this as "likely", never as the
 * server's own reason. */
export function explainGuardrailBlock(parts: readonly PromptPart[]): PromptPartMatch[] {
  const out: PromptPartMatch[] = [];
  for (const part of parts) {
    if (!part.text) continue;
    const m = findGuardrailMatch(part.text);
    if (m) out.push({ ...m, label: part.label, fixAt: part.fixAt });
  }
  return out;
}
