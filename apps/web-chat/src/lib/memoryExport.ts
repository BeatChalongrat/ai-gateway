// FIX84 row 5 — the memory export, as one function that either delivers
// the file or throws. Nothing here says "ready" — the caller toasts that
// only after this resolves, which is the whole point:
//
// Before, the page POSTed the export, then ``window.open``-ed the download
// URL and toasted "Export ready" straight away. On the desktop the URL
// went to the SYSTEM browser (Electron's window-open policy; no session
// cookie there) → a 401 page, toast still green. On any non-localhost web
// deployment the same cookie assumption breaks. The bytes now travel
// through the authenticated client (cookie + silent refresh, the file
// download path) and are handed to the browser as a blob download inside
// the app — the FIX80 will-download policy already allows that on the
// desktop.

import { ApiError } from "@/lib/api";
import { downloadFromObjectUrl } from "@/lib/fileUtil";

export interface MemoryExportClient {
  exportMemories(): Promise<{ export_id: string; filename: string; size_bytes: number; expires_at: string }>;
  raw(path: string, init?: RequestInit): Promise<Response>;
}

export interface MemoryExportResult {
  filename: string;
  sizeBytes: number;
}

export async function downloadMemoryExport(
  client: MemoryExportClient,
  deliver: (blob: Blob, filename: string) => void = deliverBlob,
): Promise<MemoryExportResult> {
  const res = await client.exportMemories();
  const file = await client.raw(`/account/exports/${encodeURIComponent(res.export_id)}/download`);
  if (!file.ok) {
    // No message on purpose: the caller shows its own dictionary string
    // (a raw "Internal Server Error" is not a sentence for the user).
    throw new ApiError(`HTTP_${file.status}`, "", file.status);
  }
  const blob = await file.blob();
  const filename = res.filename || `memories-${res.export_id}.json`;
  deliver(blob, filename);
  return { filename, sizeBytes: res.size_bytes };
}

function deliverBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  downloadFromObjectUrl(url, filename);
  // Give the browser a tick to consume the URL before revoking.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
