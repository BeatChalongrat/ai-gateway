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
