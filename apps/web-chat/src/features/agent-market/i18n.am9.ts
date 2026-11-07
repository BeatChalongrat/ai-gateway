// AM9 — where a person picks the agent's model, both levels of document
// egress are shown: the organisation's switch (the ceiling) and this agent's
// own setting under it. Thai and English, like the rest of Agent Market
// (AM11): the two objects hold the same keys, and the type makes a key that
// is in one of them only a compile error.

export const am9Th = {

  // "send as me": which mailbox an approved message leaves from
  "email.via.mailbox": "ส่งจากอีเมลของคุณที่เชื่อมต่อไว้ — ไปอยู่ในกล่องข้อความที่ส่งแล้วของคุณ",
  "email.via.relay": "ส่งผ่านเซิร์ฟเวอร์อีเมลขององค์กรในนามของคุณ ผู้รับตอบกลับถึงคุณ",
  "email.connect": "ส่งจากอีเมลของคุณ — เชื่อมต่อ Gmail",
  "email.connectOpened": "เปิดหน้าขออนุญาตในแท็บใหม่แล้ว ขอเฉพาะสิทธิ์ส่งอีเมล ไม่ขอสิทธิ์อ่าน เชื่อมต่อเสร็จแล้วกลับมาที่หน้านี้ ระบบจะตรวจให้เอง",
  "email.connectCheck": "ตรวจอีกครั้ง",
  "email.connectFailed": "เปิดหน้าเชื่อมต่ออีเมลไม่ได้ ลองอีกครั้ง",
  "email.sentVia.mailbox": "จากอีเมลของคุณ",
  "email.sentVia.relay": "ผ่านเซิร์ฟเวอร์ขององค์กร",
} as const;

export const am9En = {
  // one line: "<title> · organisation: <v> · this agent: <v>"

  // "send as me": which mailbox an approved message leaves from
  "email.via.mailbox": "Sent from your connected mailbox — it will be in your Sent folder.",
  "email.via.relay": "Sent through the organisation's mail server on your behalf. Replies come to you.",
  "email.connect": "Send from your own mailbox — connect Gmail",
  "email.connectOpened": "The consent page has opened in a new tab. It asks only for permission to send email, never to read it. Once connected, come back to this page and it checks by itself.",
  "email.connectCheck": "Check again",
  "email.connectFailed": "Could not open the mailbox connection page. Try again.",
  "email.sentVia.mailbox": "from your mailbox",
  "email.sentVia.relay": "through the organisation's server",
} as const satisfies Record<keyof typeof am9Th, string>;
