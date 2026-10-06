// AM5 — one product, then the matching engine: what a failed round says and
// offers, what a card recommends and what skipping it does, the documents a
// round left out, and the bank reconciliation agent's own words.
// Thai and English, like the rest of Agent Market (AM11): the same keys in
// both, checked by the type.

export const am5Th = {
  // a failed round
  "failed.retry": "ลองใหม่ด้วยไฟล์เดิม",
  "failed.retrying": "กำลังเริ่มรอบใหม่…",
  "failed.retried": "เริ่มรอบใหม่ด้วยไฟล์เดิมแล้ว",
  "failed.run": "ดูรายละเอียดของรอบนี้",
  "failed.generic": "รอบนี้ทำไม่สำเร็จ",
  "failed.genericNext": "ส่งไฟล์มาใหม่เพื่อเริ่มรอบนี้อีกครั้ง ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”",
  "failed.aria": "รอบนี้ทำไม่สำเร็จ",

  // what a card recommends, and what skipping it does
  "card.recommended": "ค่าแนะนำ: {label} — รอคำตอบของคุณก่อนคำนวณ",
  "card.skipLeavesOut": "ถ้าข้ามคำถามนี้ เอกสารนี้จะยังไม่ถูกรวมในรอบนี้ และอยู่ในรายการ “เอกสารที่ยังไม่ได้รวม” ซึ่งนำกลับมารวมได้ทีหลัง",

  // the documents a round left out
  "left.title": "เอกสารที่ยังไม่ได้รวม ({n})",
  "left.body": "เอกสารเหล่านี้ไม่อยู่ในไฟล์ผลงานและยอดรวมของรอบนี้",
  "left.restore": "นำกลับมารวม",
  "left.restoring": "กำลังนำกลับ…",
  "left.restored": "นำกลับมาแล้ว กำลังคำนวณใหม่ ไฟล์ชุดใหม่จะขึ้นด้านล่าง",
  "left.restoredAsk": "นำกลับมาแล้ว ต้องตอบคำถามของเอกสารนี้ก่อน คำถามจะขึ้นด้านล่าง",
  "left.superseded": "มีไฟล์รุ่นใหม่กว่าของรอบนี้ด้านล่าง รายการล่าสุดอยู่ที่การ์ดนั้น",
  "left.round": "ยังมีเอกสารที่ไม่ได้รวม {n} ฉบับ",
  "reason.unknown": "เหตุผลที่ agent บันทึกไว้",
  "outputs.check": "การตรวจของระบบ",

  // the bank reconciliation: its gate, its three numbers, who matched what
  "outputs.draft": "ร่าง — รอบนี้ยังไม่นับว่าสำเร็จ",
  "outputs.draftNext": "ควรทำต่อ: {next}",
  "outputs.adjustedBank": "ยอดหลังปรับปรุงของธนาคาร",
  "outputs.adjustedBook": "ยอดหลังปรับปรุงของบัญชี",
  "outputs.difference": "ผลต่าง",
  "outputs.matchLine": "จับคู่ได้ {matched} คู่ · ตามกฎ {rule} · คนยืนยัน {person} · รายการกระทบยอด {items}",
  "outputs.suspect": "น่าสงสัย {n}",
  "step.gate": "ตรวจว่างานครบตามเกณฑ์ของ agent",
  "step.stopped": "รอบหยุดก่อนถึงขั้นตอนถัดไป",
  "settings.yes": "ใช่",
  "settings.no": "ไม่",
} as const;

export const am5En = {
  // a failed run
  "failed.retry": "Try again with the same files",
  "failed.retrying": "Starting a new run…",
  "failed.retried": "A new run was started with the same files.",
  "failed.run": "View this run's details",
  "failed.generic": "This run did not complete",
  "failed.genericNext": "Send the files again to start this run once more. If it still does not complete, press “Report a problem”.",
  "failed.aria": "This run did not complete",

  // what a card recommends, and what skipping it does
  "card.recommended": "Recommended: {label} — waiting for your answer before calculating",
  "card.skipLeavesOut": "If you skip this question, this document is left out of this run for now and goes on the “Documents not included” list. You can include it again later.",

  // the documents a run left out
  "left.title": "Documents not included ({n})",
  "left.body": "These documents are not in this run's output files or its totals.",
  "left.restore": "Include again",
  "left.restoring": "Including…",
  "left.restored": "Included again. Recalculating — the new set of files will appear below.",
  "left.restoredAsk": "Included again. This document's question has to be answered first — it will appear below.",
  "left.superseded": "A newer version of this run's files is below. The current list is on that card.",
  "left.round": "Documents not included: {n}",
  "reason.unknown": "Reason recorded by the agent",
  "outputs.check": "System check",

  // the bank reconciliation: its gate, its three numbers, who matched what
  "outputs.draft": "Draft — this run does not yet count as successful",
  "outputs.draftNext": "Next step: {next}",
  "outputs.adjustedBank": "Adjusted balance per bank",
  "outputs.adjustedBook": "Adjusted balance per books",
  "outputs.difference": "Difference",
  "outputs.matchLine": "Matched pairs {matched} · by rule {rule} · confirmed by a person {person} · reconciling items {items}",
  "outputs.suspect": "suspect {n}",
  "step.gate": "Check the work against the agent's criteria",
  "step.stopped": "The run stopped before the next step",
  "settings.yes": "Yes",
  "settings.no": "No",
} as const satisfies Record<keyof typeof am5Th, string>;
