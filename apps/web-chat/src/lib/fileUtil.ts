// Shared helpers for file UI components.

import { tOutside } from "@/lib/i18n";

export const ALLOWED_FILE_MIMES: ReadonlySet<string> = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "text/csv",
  "text/plain",
  "text/markdown",
  "text/html",
  "application/json",
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

// Some browsers report Office formats with empty type — we accept by
// extension too. CSV from some platforms reports application/vnd.ms-excel.
// Source-code / plain-text extensions are kept in sync with the
// chat-edge-api `textLikeExtensions` map (file_service.go) — those are
// read verbatim as context for the model.
export const ALLOWED_FILE_EXTENSIONS: ReadonlySet<string> = new Set([
  "pdf", "docx", "xlsx", "pptx", "csv", "txt",
  "png", "jpg", "jpeg", "gif", "webp",
  // text / markup / data
  "json", "md", "markdown", "html", "htm", "xml", "yaml", "yml",
  "toml", "ini", "conf", "cfg", "env", "log",
  // source code
  "sql", "sh", "bash", "css", "scss", "py", "go", "js", "ts", "tsx",
  "jsx", "java", "rs", "rb", "php", "c", "cpp", "h", "swift", "kt",
  "r", "lua", "vue", "svelte",
]);

// MUST match chat-edge's MAX_FILE_SIZE_BYTES (config.go, default
// 209715200 = 200 MiB). Phase 7 fix: this was 25 MiB while the server
// accepted 200 MiB, so the client rejected files the backend would
// happily take — and the server's 413 text claimed a third number.
// If the deployment overrides MAX_FILE_SIZE_BYTES, update this too.
export const MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024; // 200 MiB
export const MAX_FILES_PER_MESSAGE = 5;

/** FIX126 F1 — the ONE gate every way a file enters the composer goes
 *  through (picker, drag-and-drop on the conversation page and on the
 *  new-chat hero, clipboard paste, chip retry). Only the picker used to
 *  cap: a drop of 20 files attached all 20, and a second paste read a
 *  stale count and went past the limit.
 *
 *  Invalid files (empty, too large, unsupported) are toasted one by one
 *  and do not take a slot. Valid files fill the free slots in the order
 *  given; the rest are left out, and ONE toast says how many were added
 *  and how many were not. `existing` is the number of live (non-error)
 *  chips already on the message, read by the caller from the store at
 *  the moment of staging — never from a render closure. */
export function admitFilesForMessage(
  files: File[],
  existing: number,
  max: number = MAX_FILES_PER_MESSAGE,
  notify: (msg: string) => void = () => {},
): { accepted: File[]; leftOut: number } {
  const slots = Math.max(0, max - existing);
  const accepted: File[] = [];
  let leftOut = 0;
  for (const f of files) {
    const v = validateFile(f);
    if (!v.ok) {
      notify(`${f.name}: ${v.reason}`);
      continue;
    }
    if (accepted.length < slots) accepted.push(f);
    else leftOut += 1;
  }
  if (leftOut > 0) {
    notify(
      tOutside("input.toast.filesCapped", {
        max: String(max),
        added: String(accepted.length),
        leftOut: String(leftOut),
      }),
    );
  }
  return { accepted, leftOut };
}

/** FIX126 F5 — the sentence an upload chip shows when its upload failed,
 *  in the active locale. The transport's own messages ("Network error",
 *  "Upload cancelled", the server's English validation text) used to be
 *  shown as-is in the Thai UI. Reads `code` / `status` by duck typing so
 *  it works for any ApiError-shaped value (tests mock the api module). */
export function uploadErrorMessage(err: unknown): string {
  const e = (err ?? {}) as { code?: unknown; status?: unknown };
  const code = typeof e.code === "string" ? e.code : "";
  const status = typeof e.status === "number" ? e.status : -1;
  switch (code) {
    case "NETWORK_ERROR":
      return tOutside("file.err.network");
    case "ABORTED":
      return tOutside("file.err.cancelled");
    case "UPLOAD_TIMEOUT":
      return tOutside("file.err.timeout");
    case "UNAUTHORIZED":
      return tOutside("file.err.sessionExpired");
  }
  switch (status) {
    case 401:
      return tOutside("file.err.sessionExpired");
    case 404:
      return tOutside("file.err.convNotFound");
    case 413:
      return tOutside("file.err.tooLarge");
    case 415:
      return tOutside("file.err.unsupported");
    case 422:
      return tOutside("file.err.empty");
  }
  return tOutside("file.err.uploadFailed");
}

export function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

export function fileExtension(filename: string): string {
  const idx = filename.lastIndexOf(".");
  if (idx === -1) return "";
  return filename.slice(idx + 1).toLowerCase();
}

export function isImageMime(contentType: string): boolean {
  return contentType.startsWith("image/");
}

// Image detection for a File must match the accept-list, not just the
// MIME — some platforms hand image files over with an empty type.
const IMAGE_EXTENSIONS: ReadonlySet<string> = new Set([
  "png", "jpg", "jpeg", "gif", "webp",
]);

export function isImageFile(file: File): boolean {
  if (file.type && isImageMime(file.type)) return true;
  return IMAGE_EXTENSIONS.has(fileExtension(file.name));
}

export function validateFile(
  file: File,
): { ok: true } | { ok: false; reason: string } {
  if (file.size === 0) return { ok: false, reason: tOutside("file.err.empty") };
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return { ok: false, reason: tOutside("file.err.tooLarge") };
  }
  const ext = fileExtension(file.name);
  const typeOk = file.type && ALLOWED_FILE_MIMES.has(file.type);
  const extOk = ext && ALLOWED_FILE_EXTENSIONS.has(ext);
  if (!typeOk && !extOk) {
    return { ok: false, reason: tOutside("file.err.unsupported") };
  }
  return { ok: true };
}

/** Trigger a browser save from an ALREADY-LOADED object URL (W2).
 *  Unlike api.downloadFile this never fetches and never revokes —
 *  the URL is owned by whoever loaded it (AuthedImage revokes on
 *  unmount), so revoking here would kill the on-screen image. */
export function downloadFromObjectUrl(url: string, filename: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export type FileIcon = "pdf" | "doc" | "sheet" | "text" | "image" | "generic";

export function iconKindFor(filename: string, contentType?: string): FileIcon {
  const ct = (contentType || "").toLowerCase();
  if (ct.startsWith("image/")) return "image";
  const ext = fileExtension(filename);
  if (ext === "pdf" || ct === "application/pdf") return "pdf";
  if (ext === "docx" || ct.includes("wordprocessing")) return "doc";
  if (ext === "xlsx" || ct.includes("spreadsheet") || ext === "csv") return "sheet";
  if (ext === "txt" || ct === "text/plain") return "text";
  return "generic";
}

export function iconEmojiFor(kind: FileIcon): string {
  switch (kind) {
    case "pdf":
    case "doc":
      return "📄";
    case "sheet":
      return "📊";
    case "text":
      return "📝";
    case "image":
      return "🖼️";
    default:
      return "📎";
  }
}

export const FILE_PICKER_ACCEPT = [
  ...Array.from(ALLOWED_FILE_EXTENSIONS, (e) => `.${e}`),
  ...Array.from(ALLOWED_FILE_MIMES),
].join(",");

/** Picker accept-list for a non-vision model (W3): same set minus the
 *  image types, so the OS file dialog itself steers away from images.
 *  The real gate stays in gateImagesForVision — accept is advisory. */
export const FILE_PICKER_ACCEPT_NO_IMAGES = [
  ...Array.from(ALLOWED_FILE_EXTENSIONS, (e) => `.${e}`).filter(
    (e) => !IMAGE_EXTENSIONS.has(e.slice(1)),
  ),
  ...Array.from(ALLOWED_FILE_MIMES).filter((m) => !isImageMime(m)),
].join(",");
