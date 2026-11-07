// FIX27 Part 1 — attached-image encoder for vision requests (Electron
// nativeImage, no new dependency). Mirrors chat-edge's image_resize.py:
// longest side ≤ 1568 px, JPEG stays JPEG, everything else becomes PNG.
// Formats nativeImage cannot decode on this build (some GIF/WebP) ship as
// their original bytes when small enough; otherwise the caller reports the
// image as unreadable instead of silently sending a text-only turn.

import { nativeImage } from "electron";
import { IMAGE_MIMES, type ImageEncoder } from "./attachments";

export const VISION_MAX_DIM = 1568;
const RAW_FALLBACK_MAX_BYTES = 4 * 1024 * 1024;

export const encodeImageForVision: ImageEncoder = async (bytes, mime) => {
  const m = (mime || "").toLowerCase();
  let img = nativeImage.createFromBuffer(bytes);
  if (img.isEmpty()) {
    if (IMAGE_MIMES.has(m) && bytes.length <= RAW_FALLBACK_MAX_BYTES) return `data:${m};base64,${bytes.toString("base64")}`;
    return null;
  }
  const { width, height } = img.getSize();
  const longest = Math.max(width, height);
  if (longest > VISION_MAX_DIM) {
    const scale = VISION_MAX_DIM / longest;
    img = img.resize({ width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)), quality: "good" });
  }
  if (m === "image/jpeg") return `data:image/jpeg;base64,${img.toJPEG(88).toString("base64")}`;
  return `data:image/png;base64,${img.toPNG().toString("base64")}`;
};
