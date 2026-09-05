// FIX62 — explicit image-GENERATION intent, detected in the composer.
//
// Why here and not only in chat-edge: chat-edge already has this list
// (image_intent.go, Phase D) but only the WEB path passes through
// chat-edge. The desktop opens this same page and sends its turns through
// its own coordinator straight to the backend, so a rule that lives in
// chat-edge never reaches it. The composer is the one place both clients
// share. The phrases are chat-edge's, verbatim, so the two lists cannot
// drift apart in meaning; keep them in step when editing either.
//
// Deliberately SPECIFIC action phrases — bare "draw" / "create" / "design"
// are excluded on purpose ("draw a conclusion", "create a plan").

const THAI_IMAGE_KEYWORDS = [
  "สร้างรูป", "วาดรูป", "ทำรูป", "สร้างภาพ", "วาดภาพ",
  "ออกแบบรูป", "วาดให้", "ช่วยวาด", "สร้างรูปภาพ", "ทำภาพ",
];

const EN_IMAGE_KEYWORDS = [
  "draw me", "draw a picture", "draw an image", "draw a pic",
  "generate an image", "generate a image", "generate image",
  "generate a picture", "create an image", "create a image",
  "create image", "create a picture", "make an image", "make a image",
  "make a picture", "paint me", "illustrate a", "sketch a",
];

/** True when the message is an explicit request to generate an image. */
export function detectImageIntent(message: string): boolean {
  const lower = message.toLowerCase();
  return (
    THAI_IMAGE_KEYWORDS.some((kw) => lower.includes(kw)) ||
    EN_IMAGE_KEYWORDS.some((kw) => lower.includes(kw))
  );
}
