// AM24 — what a waiting run waits for. Thai and English, like the rest of Agent Market (AM11): the two objects hold
// the same keys, and the type makes a key that is in one of them only a compile error.
//
// A run parked until its budget is raised was titled "รอคำตอบของคุณ" — nothing is asked of the person; an admin
// raises the budget. And the dashboard's "รอคำตอบอยู่" counted the runs that wait for their model or for budget too:
// a person reading it thought people were being asked things. Each is now said for what it is.

export const am24Th = {
  "progress.title.waiting_budget": "รอเพิ่มงบ",
  "dash.waitingModel": "{n} งานรอโมเดล",
  "dash.waitingBudget": "{n} งานรอเพิ่มงบ",
} as const;

export const am24En: Record<keyof typeof am24Th, string> = {
  "progress.title.waiting_budget": "Waiting for budget",
  "dash.waitingModel": "{n} waiting for the model",
  "dash.waitingBudget": "{n} waiting for budget",
};
