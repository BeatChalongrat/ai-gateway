// AM21 — a person sees the finished file before they send it: the document's first pages and the deck's
// slides as pictures on the files card, made from the delivered file itself. Thai and English, like the
// rest of Agent Market (AM11): the two objects hold the same keys.

export const am21Th = {
  "outputs.preview": "ดูก่อนส่ง",
  "outputs.previewHide": "ซ่อนภาพ",
  "outputs.previewPending": "กำลังทำภาพของหน้าเอกสารจากไฟล์ที่ส่งมอบ…",
  "outputs.previewUnavailable": "ยังทำภาพไม่ได้: {reason}",
  "outputs.previewOf": "ภาพจากไฟล์ที่ส่งมอบจริง ไม่ได้วาดใหม่จากข้อมูล",
  "outputs.previewPage": "หน้า {n} จาก {total}",
  "outputs.previewMore": "แสดง {n} หน้าแรกจากทั้งหมด {total} หน้า",
  "outputs.previewClose": "ย่อภาพ",
} as const;

export const am21En: Record<keyof typeof am21Th, string> = {
  "outputs.preview": "Preview before sending",
  "outputs.previewHide": "Hide preview",
  "outputs.previewPending": "Making the pictures of the pages from the delivered file…",
  "outputs.previewUnavailable": "The pictures could not be made: {reason}",
  "outputs.previewOf": "Pictures of the delivered file itself, not drawn again from the data",
  "outputs.previewPage": "Page {n} of {total}",
  "outputs.previewMore": "Showing the first {n} pages of {total}",
  "outputs.previewClose": "Shrink",
};
