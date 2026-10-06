// AM11b — quiet the agent's room. Thai and English, like the rest of Agent
// Market (AM11): the two objects hold the same keys, and the type makes a key
// that is in one of them only a compile error.

export const am11bTh = {
  // B — the model menu is the chat's own: a model that cannot be used for this agent is not listed, and
  // when that is why the list is shorter, this one line says so
  "model.outsideAbsent": "โมเดลนอกองค์กรไม่อยู่ในรายการ — agent นี้ยังไม่ได้รับอนุญาตให้ส่งข้อมูลออกนอกองค์กร",
} as const;

export const am11bEn = {
  "model.outsideAbsent": "Models outside the organisation are not listed — this agent may not send data outside the organisation.",
} as const satisfies Record<keyof typeof am11bTh, string>;
