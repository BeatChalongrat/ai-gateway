// FIX130 — did the user EXPLICITLY ask to remember something in this turn?
//
// A port of chat-edge's ExplicitRememberRequest
// (services/chat-edge-api/internal/service/explicit_remember.go): same word
// lists, same algorithm, and both are tested against the one table
// (testdata/explicit_remember_cases.json, read by explicit-remember.test.ts),
// so the two clients cannot drift.
//
// Why it exists: the `remember` tool wrote an ACTIVE memory — injected into
// every later chat, on the web too — whenever the model decided to call it.
// The model's decision is not the user's request: on the test stack a model
// saved plain self-descriptions nobody asked it to keep, and anything that can
// steer the model (a fetched page, a tool result) could plant an always-on
// note the same way. A note is saved as active only when the turn's own user
// message asks for it; otherwise it becomes a pending suggestion the user
// confirms on the Memory page.
//
// Plain substring rules on purpose, not regexes: they must behave the same in
// Go and JavaScript, and Thai has no word boundaries to lean on. The negatives
// are removed first — Thai words that merely START with จำ (จำแนก classify,
// จำลอง simulate, จำนวน amount, …) and English questions or statements ABOUT
// remembering — so the positives cannot fire on them.

const NEGATIVES: readonly string[] = [
  // Thai compounds that begin with จำ but have nothing to do with memory.
  "จำแนก", "จำลอง", "จำเป็น", "จำหน่าย", "จำกัด", "จำนวน", "จำนอง", "จำพวก",
  "จำเพาะ", "จำเลย", "จำศีล", "จำยอม", "จำใจ", "จำต้อง",
  // Asking whether the assistant remembers is not asking it to.
  "จำได้ไหม", "จำได้มั้ย", "จำได้หรือเปล่า", "จำได้รึเปล่า",
  "do you remember", "did you remember", "don't remember", "dont remember",
  "do not remember", "can't remember", "cant remember", "cannot remember",
  "i remember", "remembered", "remembering", "remember when",
];

const PHRASES: readonly string[] = [
  // Thai
  "จำไว้", "ช่วยจำ", "จำด้วย", "จำนะ", "จำให้", "จดไว้", "จดจำ",
  "บันทึกไว้ว่า", "บันทึกไว้ด้วย", "บันทึกไว้นะ", "บันทึกไว้หน่อย",
  "บันทึกความจำ", "บันทึกลงความจำ", "บันทึกเป็นความจำ", "เก็บไว้ในความจำ",
  "อย่าลืม", "จากนี้ไป", "ตั้งแต่นี้ไป", "ต่อจากนี้ไป",
  // English
  "remember", "don't forget", "dont forget", "do not forget",
  "note that", "take note", "make a note", "keep in mind",
  "save this", "save that", "store this", "memorize", "memorise",
  "for future reference", "from now on", "going forward",
];

/** True when the user's message explicitly asks the assistant to remember
 *  something (or sets a standing "from now on" rule). Stating a fact about
 *  oneself is NOT a request: that is what the review queue is for. */
export function explicitRememberRequest(text: string | null | undefined): boolean {
  let s = (text ?? "").split("’").join("'").toLowerCase();
  if (s.trim() === "") return false;
  for (const n of NEGATIVES) s = s.split(n).join(" ");
  return PHRASES.some((p) => s.includes(p));
}
