// Explicit transfer between granted local folders and conversation storage.
// The coordinator owns consent; these checks still run in Skip mode.
import fs from "node:fs/promises";
import path from "node:path";
import type { ChatEdgeClient } from "./chat-edge-client";
import type { HostToolContext, HostToolResult } from "./host-tools";

export const LOCAL_FILE_MAX_BYTES = 25 * 1024 * 1024;
const MIME: Record<string, string> = {
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".pdf": "application/pdf", ".csv": "text/csv", ".tsv": "text/plain",
  ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4", ".wav": "audio/wav",
};

function inside(target: string, root: string): boolean {
  const relative = path.relative(root, target);
  return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

async function checkedPath(raw: unknown, grants: readonly string[], forbidden: readonly string[], writing: boolean): Promise<string> {
  if (typeof raw !== "string" || !path.isAbsolute(raw) || raw.includes("\0")) {
    throw new Error("Choose an absolute file path in a granted folder.");
  }
  // Reject device paths, network shares, alternate data streams and Win32
  // aliases. They must not name a different file from the consent card.
  if (process.platform === "win32" && (!/^[a-z]:[\\/]/i.test(raw) || /[:<>"|?*]/.test(raw.slice(2)) || raw.split(/[\\/]/).slice(1).some((s) => /[. ]$/.test(s) && s !== "." && s !== "..") || /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(path.basename(raw)))) {
    throw new Error("Device paths, network shares and alternate data streams are not supported.");
  }
  const target = path.resolve(raw);
  if (forbidden.some((root) => inside(target, path.resolve(root)))) throw new Error("System and application-state folders cannot be transferred.");
  if (!MIME[path.extname(target).toLowerCase()]) throw new Error("This file type is not supported for local document transfer.");
  if (!grants.some((root) => inside(target, path.resolve(root)))) throw new Error("The file must be inside a folder granted in Desktop Settings.");
  const existing = writing ? path.dirname(target) : target;
  // Check each component, including the root of the grant, so junctions and
  // symbolic links cannot redirect an approved path to another folder.
  let cursor = path.parse(existing).root;
  for (const part of existing.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    if ((await fs.lstat(cursor)).isSymbolicLink()) throw new Error("Symbolic links and junctions are not supported for local document transfer.");
  }
  const canonical = await fs.realpath(existing);
  const actual = writing ? path.join(canonical, path.basename(target)) : canonical;
  if (forbidden.some((root) => inside(actual, path.resolve(root)))) throw new Error("System and application-state folders cannot be transferred.");
  const roots = await Promise.all(grants.map((root) => fs.realpath(root).catch(() => "")));
  if (!roots.some((root) => root && inside(actual, root))) throw new Error("The resolved file is outside the granted folders.");
  return actual;
}

export async function transferLocalFile(name: string, args: Record<string, unknown>, ctx: HostToolContext, chat: ChatEdgeClient): Promise<HostToolResult> {
  try {
    ctx.signal.throwIfAborted();
    const writing = name === "save_conversation_file";
    const target = await checkedPath(args["path"], ctx.grantedFolders ?? [], ctx.forbiddenFolders ?? [], writing);
    if (!writing) {
      const handle = await fs.open(target, "r");
      let bytes: Buffer;
      try {
        const stat = await handle.stat();
        if (!stat.isFile() || stat.size > LOCAL_FILE_MAX_BYTES) throw new Error("Choose a regular file of at most 25 MB.");
        const buffer = Buffer.alloc(stat.size + 1);
        let offset = 0;
        while (offset < buffer.length) {
          const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, null);
          if (!bytesRead) break;
          offset += bytesRead;
        }
        if (offset !== stat.size) throw new Error("The file changed while it was being read; retry after saving it.");
        bytes = buffer.subarray(0, offset);
      } finally {
        await handle.close();
      }
      ctx.signal.throwIfAborted();
      const filename = path.basename(target);
      const contentType = MIME[path.extname(target).toLowerCase()]!;
      const id = await chat.uploadFileBytes(ctx.conversationId, bytes, filename, contentType, ctx.signal);
      if (!id) throw new Error("Upload failed; the file was not attached to this chat.");
      return {
        ok: true, imageDataUri: null,
        text: JSON.stringify({ file_id: id, filename, message: "Copied to this conversation. Pass file_id in run_python.files to edit it; the original local file is unchanged." }),
        attachedFiles: [{ id, filename, content_type: contentType, file_size_bytes: bytes.length, extraction_status: "pending" }],
      };
    }
    const id = args["file_id"];
    if (typeof id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) throw new Error("A file_id from this conversation is required.");
    const files = await chat.listFiles(ctx.conversationId);
    if (!files?.some((file) => file.id === id)) throw new Error("The file does not belong to this conversation or could not be verified.");
    const downloaded = await chat.downloadFileBytes(id, ctx.signal);
    if (!downloaded || downloaded.bytes.length > LOCAL_FILE_MAX_BYTES) throw new Error("Download failed or the file exceeds 25 MB.");
    ctx.signal.throwIfAborted();
    // Revalidate after network IO. Exclusive creation protects originals and
    // refuses an existing file, symbolic link or concurrently created output.
    const destination = await checkedPath(args["path"], ctx.grantedFolders ?? [], ctx.forbiddenFolders ?? [], true);
    await fs.writeFile(destination, downloaded.bytes, { flag: "wx", mode: 0o600 });
    return { ok: true, imageDataUri: null, text: JSON.stringify({ path: destination, bytes: downloaded.bytes.length, message: "Saved a new local file." }) };
  } catch (error) {
    return { ok: false, imageDataUri: null, text: `File transfer failed: ${(error as Error).message}` };
  }
}
