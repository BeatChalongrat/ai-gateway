// AM11b — quiet the agent's room. Thai and English, like the rest of Agent
// Market (AM11): the two objects hold the same keys, and the type makes a key
// that is in one of them only a compile error.

export const am11bTh = {
  // B — the model menu is the chat's own: a model that cannot be used for this agent is not listed, and
  // when that is why the list is shorter, this one line says so
  "model.outsideAbsent": "โมเดลนอกองค์กรไม่อยู่ในรายการ — agent นี้ยังไม่ได้รับอนุญาตให้ส่งข้อมูลออกนอกองค์กร",

  // D — the agent's page is a workspace, not a brochure: what it does, what to give it, what comes back;
  // everything else is behind this one word
  "detail.more": "รายละเอียด",
  // … and the workspace's footer is one short line, not a list
  "ws.footer.files": "agent นี้อ่านเฉพาะไฟล์ที่คุณส่งให้",
  "ws.footer.request": "agent นี้อ่านเฉพาะคำขอและไฟล์ที่คุณส่ง กับหน้าเว็บสาธารณะที่มันเปิดเอง",

  // E — an agent that does what it is asked: the greeting says it takes instructions, and a form when it fills one
  "greeting.also": "อยากให้ทำต่างจากนี้ บอกได้เลย เช่น “ไม่ต้องใส่กราฟ” หรือ “ขอสั้น ๆ หน้าเดียว”",
  "greeting.alsoForm": "อยากให้ทำต่างจากนี้ บอกได้เลย เช่น “ไม่ต้องใส่กราฟ” หรือ “ขอสั้น ๆ หน้าเดียว” — หรือแนบแบบฟอร์มที่อยากให้กรอก",
} as const;

export const am11bEn = {
  "model.outsideAbsent": "Models outside the organisation are not listed — this agent may not send data outside the organisation.",

  "detail.more": "Details",
  "ws.footer.files": "This agent reads only the files you give it.",
  "ws.footer.request": "This agent reads only the request and the files you send, and the public web pages it opens itself.",

  "greeting.also": "Want it done differently? Just say so — “no charts”, or “keep it to one page”.",
  "greeting.alsoForm": "Want it done differently? Just say so — “no charts”, or “keep it to one page” — or attach a form you want filled in.",
} as const satisfies Record<keyof typeof am11bTh, string>;
