// AM16 — what a run says on the face of its own card when its documents were
// sent to a model outside the organisation as they were typed. Thai and English,
// like the rest of Agent Market (AM11): the two objects hold the same keys, and
// the type makes a key that is in one of them only a compile error.
//
// Masking is the admin's own switch per agent, off by default; it no longer
// follows whether the documents may leave. So a run can send its documents out
// unmasked — by an admin's two choices — and the owner's condition for that is
// that the run says so plainly, in both languages, on the card and not in its
// details. The run says it from what happened (the runner notes each call), not
// from the switches: a page read by an outside model is an image, which nothing
// masks, and it is said for that too.

export const am16Th = {
  "sentOut.title": "เอกสารของรอบนี้ถูกส่งออกนอกองค์กร และไม่ได้ปิดบังข้อมูล",
  "sentOut.detail": "โมเดลนอกองค์กรได้รับเอกสารตามที่เป็นอยู่ ผู้ดูแลตั้งค่านี้ให้ agent นี้ไว้",
  "outputs.masking.offOut": "สิ่งที่ส่งให้โมเดลในรอบนี้ไม่ถูกปิดบัง",
} as const;

export const am16En: Record<keyof typeof am16Th, string> = {
  "sentOut.title": "This run's documents were sent outside the organisation and were not masked",
  "sentOut.detail": "A model outside the organisation was given them as they are. An admin has set this for this agent",
  "outputs.masking.offOut": "What was sent to a model in this run was not masked",
};
