import type { ChatEdgeClient } from "./chat-edge-client";

/** Only conversation-owned attachments may become external image references. */
export async function imageSources(client: ChatEdgeClient, conversationId: string, ids: unknown, signal: AbortSignal): Promise<string[]> {
  if (ids === undefined) return [];
  if (!Array.isArray(ids) || ids.length > 4 || ids.some(id => typeof id !== "string" || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id))) {
    throw new Error("source_file_ids must contain up to 4 conversation image IDs.");
  }
  if (!ids.length) return [];
  const files = await client.listFiles(conversationId);
  if (!files || ids.some(id => !files.some(file => file.id === id))) throw new Error("Reference image must belong to this conversation.");
  const sources: string[] = [];
  let total = 0;
  for (const id of new Set<string>(ids)) {
    if (signal.aborted) throw new Error("Image editing cancelled.");
    const image = await client.downloadFileBytes(id, signal);
    if (!image || !["image/png", "image/jpeg", "image/webp"].includes(image.mime)) throw new Error("Reference images must be PNG, JPEG or WebP.");
    total += image.bytes.length;
    if (!image.bytes.length || image.bytes.length > 4 * 1024 * 1024 || total > 12 * 1024 * 1024) throw new Error("Reference images exceed 4 MiB each / 12 MiB total.");
    sources.push(`data:${image.mime};base64,${image.bytes.toString("base64")}`);
  }
  return sources;
}
