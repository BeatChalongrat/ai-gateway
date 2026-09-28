// AM5 — one product, then the matching engine: what a failed round says and
// offers, what a card recommends and what skipping it does, the documents a
// round left out, and the bank reconciliation agent's own words.
// Thai, like the rest of Agent Market.

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
} as const;
