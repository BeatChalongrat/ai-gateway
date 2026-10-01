// AM9 — where a person picks the agent's model, both levels of document
// egress are shown: the organisation's switch (the ceiling) and this agent's
// own setting under it. Thai, like the rest of Agent Market.

export const am9Th = {
  "egress.title": "เอกสารส่งไปโมเดลนอกองค์กร",
  "egress.org": "องค์กร: {v}",
  "egress.agent": "agent นี้: {v}",
  "egress.yes": "อนุญาต",
  "egress.no": "ไม่อนุญาต",
  "egress.agentDefault": "ไม่อนุญาต (ค่าตั้งต้นของ agent ที่อ่านข้อมูลบัญชี)",
  "egress.ceiling": "agent นี้อนุญาต แต่องค์กรไม่อนุญาต จึงไม่ส่ง",
} as const;
