// Phase 7 — Enter-to-send decision, isolated so it can be unit-tested
// without mounting the whole composer.
//
// The IME rule: while an input method editor is composing (Japanese,
// Chinese, Korean, and predictive keyboards on Android), Enter COMMITS
// the candidate — it does not mean "send". Sending then would ship
// half-composed text and drop the rest. `isComposing` is the modern
// signal; keyCode 229 is the legacy one older IMEs still emit.
//
// Thai (Kedmanee) types characters directly and never composes, so
// Enter-to-send is unchanged for the primary audience.

export interface KeyLike {
  key: string;
  shiftKey: boolean;
  isComposing?: boolean;
  keyCode?: number;
}

export function isImeComposing(e: KeyLike): boolean {
  return e.isComposing === true || e.keyCode === 229;
}

/** True only for a plain Enter that is NOT an IME commit. */
export function shouldSendOnEnter(e: KeyLike): boolean {
  if (isImeComposing(e)) return false;
  return e.key === "Enter" && !e.shiftKey;
}
