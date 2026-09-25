// Agent Market (AM1) — typed client for chat-edge's /api/v1/agent-market.
//
// Every JSON call rides api.raw() (base URL, the HttpOnly session cookie and
// the 401 → refresh → retry contract). Errors arrive as
// {error:{code,message}} with a Thai message meant for the person; AmError
// carries it unchanged so a screen can show it as is.
//
// Uploads use XHR (progress + the same refresh-once contract as
// api.uploadFile) because api.raw() would label a FormData body JSON.

import { api, CHAT_API_BASE, sharedRefresh } from "@/lib/api";
import { redirectToPortalLogin } from "@/lib/auth";

import { tAm } from "./i18n";
import type {
  AmFile,
  AnswerBody,
  Availability,
  CatalogAgent,
  CatalogItem,
  Computed,
  Default,
  InstallDetail,
  InstallResponse,
  InstallRow,
  Run,
  RunDetail,
  ThemeResponse,
  ThreadItem,
} from "./types";

const BASE = "/agent-market";
const UPLOAD_TIMEOUT_MS = 300_000;

export class AmError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "AmError";
  }
}

/** The person-facing sentence of any error (the server's Thai message when it sent one). */
export function errorMessage(err: unknown): string {
  if (err instanceof AmError && err.message) return err.message;
  return tAm("error.generic");
}

function parse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function toError(res: Response): Promise<AmError> {
  let body: unknown = null;
  try {
    body = parse(await res.text());
  } catch {
    /* no body */
  }
  const env = body as { error?: { code?: string; message?: string } } | null;
  return new AmError(env?.error?.code ?? `HTTP_${res.status}`, env?.error?.message || tAm("error.generic"), res.status);
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await api.raw(`${BASE}${path}`, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new AmError("NETWORK_ERROR", tAm("error.network"), 0);
  }
  if (!res.ok) throw await toError(res);
  return parse(await res.text()) as T;
}

function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return call<T>(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function saveBlob(path: string, filename: string): Promise<void> {
  const res = await api.raw(`${BASE}${path}`);
  if (!res.ok) throw await toError(res);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke once the download has had time to start (mirrors api.downloadFile).
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export interface UploadOptions {
  clientId?: string;
  onProgress?: (percent: number) => void;
  signal?: AbortSignal;
}

function uploadOnce(installId: string, file: File, opts: UploadOptions, retried: boolean): Promise<AmFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${CHAT_API_BASE}${BASE}/installs/${encodeURIComponent(installId)}/files`);
    xhr.withCredentials = true;
    xhr.timeout = UPLOAD_TIMEOUT_MS;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts.onProgress) opts.onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status === 401 && !retried) {
        sharedRefresh().then((outcome) => {
          if (outcome === "ok") {
            uploadOnce(installId, file, opts, true).then(resolve, reject);
            return;
          }
          if (outcome === "rejected") redirectToPortalLogin();
          reject(new AmError("UNAUTHORIZED", tAm("error.session"), 401));
        });
        return;
      }
      const body = parse(xhr.responseText) as { file?: AmFile; error?: { code?: string; message?: string } } | null;
      if (xhr.status >= 200 && xhr.status < 300 && body?.file) {
        resolve(body.file);
        return;
      }
      reject(new AmError(body?.error?.code ?? `HTTP_${xhr.status}`, body?.error?.message || tAm("error.upload"), xhr.status));
    };
    xhr.onerror = () => reject(new AmError("NETWORK_ERROR", tAm("error.network"), 0));
    xhr.onabort = () => reject(new AmError("ABORTED", tAm("error.aborted"), 0));
    xhr.ontimeout = () => reject(new AmError("UPLOAD_TIMEOUT", tAm("error.uploadTimeout"), 0));
    if (opts.signal) {
      if (opts.signal.aborted) {
        xhr.abort();
        return;
      }
      opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    const form = new FormData();
    form.append("file", file);
    if (opts.clientId) form.append("client_id", opts.clientId);
    xhr.send(form);
  });
}

const id = (s: string) => encodeURIComponent(s);

export const amApi = {
  availability: () => call<Availability>("/availability"),
  catalog: () => call<{ agents: CatalogAgent[] | null }>("/catalog"),
  catalogItem: (slug: string) => call<CatalogItem>(`/catalog/${id(slug)}`),

  install: (body: { slug: string; idempotency_key: string; self_test: boolean; settings?: Record<string, unknown>; theme?: unknown }) =>
    send<InstallResponse>("POST", "/installs", body),
  installs: () => call<{ installs: InstallRow[] | null }>("/installs"),
  getInstall: (installId: string) => call<InstallDetail>(`/installs/${id(installId)}`),
  removeInstall: (installId: string) => send<{ removed: boolean }>("DELETE", `/installs/${id(installId)}`),

  defaults: (installId: string) => call<{ defaults: Default[] | null }>(`/installs/${id(installId)}/defaults`),
  putDefault: (installId: string, key: string, value: unknown, label?: string) =>
    send<{ saved: boolean }>("PUT", `/installs/${id(installId)}/defaults/${id(key)}`, label ? { value, label } : { value }),
  deleteDefault: (installId: string, key: string) =>
    send<{ removed: boolean }>("DELETE", `/installs/${id(installId)}/defaults/${id(key)}`),

  selfTest: (installId: string) => send<{ run: Run }>("POST", `/installs/${id(installId)}/self-test`),
  runs: (installId: string) => call<{ runs: Run[] | null }>(`/installs/${id(installId)}/runs`),
  thread: (installId: string, after = 0) =>
    call<{ items: ThreadItem[] | null; cursor: number }>(`/installs/${id(installId)}/thread?after=${after}`),
  /** The raw SSE response; the caller parses it (lib/stream parseSSE). */
  events: (installId: string, after: number, signal: AbortSignal) =>
    api.raw(`${BASE}/installs/${id(installId)}/events?after=${after}`, { signal }),

  upload: (installId: string, file: File, opts: UploadOptions = {}) => uploadOnce(installId, file, opts, false),
  message: (installId: string, body: { text: string; file_ids: string[]; client_id: string }) =>
    send<{ item: ThreadItem; route?: string; run_id?: string; duplicate?: boolean }>("POST", `/installs/${id(installId)}/messages`, body),

  mappingPreview: (installId: string, fileId: string) =>
    send<{ job: Run }>("POST", "/mapping-preview", { install_id: installId, file_id: fileId }),
  answerCard: (cardId: string, body: AnswerBody) =>
    send<{ changed: boolean; status?: string }>("POST", `/cards/${id(cardId)}/answer`, body),

  run: (runId: string) => call<RunDetail>(`/runs/${id(runId)}`),
  computed: (runId: string) => call<Computed>(`/runs/${id(runId)}/computed`),
  cancelRun: (runId: string) => send<{ run: Run }>("POST", `/runs/${id(runId)}/cancel`),
  formats: (runId: string, formats: string[]) => send<{ job: Run }>("POST", `/runs/${id(runId)}/formats`, { formats }),

  downloadArtifact: (artifactId: string, filename: string) => saveBlob(`/artifacts/${id(artifactId)}/download`, filename),
  downloadFile: (fileId: string, filename: string) => saveBlob(`/files/${id(fileId)}/download`, filename),

  theme: () => call<ThemeResponse>("/theme"),
  putTheme: (theme: { id: string; company?: string }) => send<{ version: number }>("PUT", "/theme", theme),
};

/** A fresh client id (idempotency key) for a message, a chip or an install. */
export function newClientId(): string {
  const c = typeof crypto !== "undefined" ? (crypto as Crypto & { randomUUID?: () => string }) : undefined;
  if (c?.randomUUID) return c.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    return (ch === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}
