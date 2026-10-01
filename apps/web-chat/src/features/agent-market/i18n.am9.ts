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

  // "send as me": which mailbox an approved message leaves from
  "email.via.mailbox": "ส่งจากอีเมลของคุณที่เชื่อมต่อไว้ — ไปอยู่ในกล่องข้อความที่ส่งแล้วของคุณ",
  "email.via.relay": "ส่งผ่านเซิร์ฟเวอร์อีเมลขององค์กรในนามของคุณ ผู้รับตอบกลับถึงคุณ",
  "email.connect": "ส่งจากอีเมลของคุณ — เชื่อมต่อ Gmail",
  "email.connectOpened": "เปิดหน้าขออนุญาตในแท็บใหม่แล้ว ขอเฉพาะสิทธิ์ส่งอีเมล ไม่ขอสิทธิ์อ่าน เชื่อมต่อเสร็จแล้วกดตรวจอีกครั้ง",
  "email.connectCheck": "ตรวจอีกครั้ง",
  "email.connectFailed": "เปิดหน้าเชื่อมต่ออีเมลไม่ได้ ลองอีกครั้ง",
  "email.sentVia.mailbox": "จากอีเมลของคุณ",
  "email.sentVia.relay": "ผ่านเซิร์ฟเวอร์ขององค์กร",
} as const;
