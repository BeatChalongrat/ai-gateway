// Shared helpers for file UI components.

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

export const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024; // 25 MiB
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

export function validateFile(
  file: File,
): { ok: true } | { ok: false; reason: string } {
  if (file.size === 0) return { ok: false, reason: "Empty file" };
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return { ok: false, reason: "File is larger than 25 MB" };
  }
  const ext = fileExtension(file.name);
  const typeOk = file.type && ALLOWED_FILE_MIMES.has(file.type);
  const extOk = ext && ALLOWED_FILE_EXTENSIONS.has(ext);
  if (!typeOk && !extOk) {
    return { ok: false, reason: "Unsupported file type" };
  }
  return { ok: true };
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
