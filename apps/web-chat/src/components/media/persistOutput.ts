// FIX81 ③ — a finished media job's bytes become a durable ConversationFile.
//
// Before this, a generated track or clip lived only in a blob object URL:
// "Generate another" revoked it and a reload lost it (no jobs-list endpoint
// exists to rediscover the job). The user had a player, not a file. This
// helper reads the already-fetched bytes back from the object URL (zero
// re-fetch from the server) and posts them through the one delivery door
// (POST /conversations/{id}/files/generated), so the output lands in the
// conversation's files — durable, previewable, downloadable.

import { api } from "@/lib/api";
import type { FileAttachment } from "@/lib/types";

/** Blob bytes → base64, chunked so a large clip never blows the stack. */
export async function blobToBase64(blob: Blob): Promise<string> {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < buf.length; i += CHUNK) {
    bin += String.fromCharCode(...buf.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

/** A filename for a media output: prompt slug + the blob's extension. */
export function mediaOutputFilename(prompt: string, contentType: string): string {
  const ext = contentType.includes("mp4")
    ? ".mp4"
    : contentType.includes("webm")
      ? ".webm"
      : contentType.includes("wav")
        ? ".wav"
        : contentType.includes("ogg")
          ? ".ogg"
          : contentType.startsWith("video/")
            ? ".mp4"
            : ".mp3";
  const slug = prompt
    .replace(/[\\/:*?"<>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40)
    .trim()
    .replace(/ /g, "-");
  return (slug || "generated-media") + ext;
}

export async function persistMediaOutput(
  conversationId: string,
  objectUrl: string,
  prompt: string,
  generatedBy: string,
): Promise<FileAttachment | null> {
  try {
    const blob = await (await fetch(objectUrl)).blob();
    if (blob.size === 0) return null;
    const contentType = blob.type || "application/octet-stream";
    return await api.saveGeneratedConversationFile(conversationId, {
      filename: mediaOutputFilename(prompt, contentType),
      content_type: contentType,
      data_base64: await blobToBase64(blob),
      generated_by: generatedBy,
    });
  } catch {
    // Best-effort: the player still works; the caller shows no saved-note.
    return null;
  }
}
