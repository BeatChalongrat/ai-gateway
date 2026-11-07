// AM15 — a run that waits for its model. Thai and English, like the rest of
// Agent Market (AM11): the two objects hold the same keys, and the type makes a
// key that is in one of them only a compile error.
//
// A run that waits is "awaiting_input", and the title of every such run was
// "รอคำตอบของคุณ" — true of a run that asked something, and wrong of one that
// stopped because the model it may use cannot be reached: the person would look
// for a question that is not there. Nothing is asked of them; the run goes on
// by itself when the model is back.

export const am15Th = {
  "progress.title.waiting_model": "รอโมเดล",
  "progress.waitedModel": "รอโมเดลมาแล้ว {d}",
} as const;

export const am15En: Record<keyof typeof am15Th, string> = {
  "progress.title.waiting_model": "Waiting for the model",
  "progress.waitedModel": "Waiting for the model for {d}",
};
