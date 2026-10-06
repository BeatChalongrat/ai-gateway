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
import { useI18nStore } from "@/lib/i18n";

import { tAm } from "./i18n";
import type {
  AgentDashboard,
  AgentModels,
  AgentThread,
  AmFile,
  AmNotification,
  AnswerBody,
  Availability,
  BudgetState,
  CatalogAgent,
  CatalogItem,
  CatalogSample,
  Computed,
  Datasheet,
  DeclaredSetting,
  Default,
  EmailDraft,
  EmailSend,
  Estimate,
  Install,
  InstallDetail,
  InstallResponse,
  InstallRow,
  Person,
  ProblemReport,
  RailPrefs,
  RailResponse,
  Reminder,
  ReminderRule,
  RemindersResponse,
  Review,
  Run,
  RunDetail,
  RunExceptionRow,
  RunFormDetail,
  SavedTemplate,
  Share,
  SharedItem,
  SharedRun,
  StorageInfo,
  TemplateSlot,
  ThemeResponse,
  ThreadItem,
  UpgradeInfo,
  WebCall,
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

/** AM11: the screen's language, said with every call — the server answers in it (an error, an estimate,
 *  what it writes into a thread), and a run started now is in it. Accept-Language needs no CORS
 *  allowance; the server reads it only when it is exactly one tag, which is what this sends. */
export function withLang(init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers ?? undefined);
  headers.set("Accept-Language", useI18nStore.getState().locale);
  return { ...init, headers };
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await api.raw(`${BASE}${path}`, withLang(init));
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
  const res = await api.raw(`${BASE}${path}`, withLang());
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

async function blobUrl(path: string): Promise<string> {
  const res = await api.raw(`${BASE}${path}`, withLang());
  if (!res.ok) throw await toError(res);
  return URL.createObjectURL(await res.blob());
}

export interface UploadOptions {
  clientId?: string;
  onProgress?: (percent: number) => void;
  signal?: AbortSignal;
}

/** POST /installs/{id}/template: the stored file and the inspect job to poll. */
export interface TemplateUpload {
  file: AmFile;
  kind: string;
  job: Run;
}

/** One multipart POST of `file` to `path`; `pick` takes the answer out of a 2xx body. */
function uploadOnce<T>(path: string, file: File, opts: UploadOptions, retried: boolean, pick: (body: unknown) => T | undefined): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${CHAT_API_BASE}${path}`);
    xhr.setRequestHeader("Accept-Language", useI18nStore.getState().locale); // AM11: as withLang()
    xhr.withCredentials = true;
    xhr.timeout = UPLOAD_TIMEOUT_MS;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts.onProgress) opts.onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status === 401 && !retried) {
        sharedRefresh().then((outcome) => {
          if (outcome === "ok") {
            uploadOnce(path, file, opts, true, pick).then(resolve, reject);
            return;
          }
          if (outcome === "rejected") redirectToPortalLogin();
          reject(new AmError("UNAUTHORIZED", tAm("error.session"), 401));
        });
        return;
      }
      const body = parse(xhr.responseText) as { error?: { code?: string; message?: string } } | null;
      const picked = xhr.status >= 200 && xhr.status < 300 ? pick(body) : undefined;
      if (picked !== undefined) {
        resolve(picked);
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
/** AM4: the thread a workspace shows (absent = the install's first). */
const threadQ = (thread?: string | null) => (thread ? `&thread=${encodeURIComponent(thread)}` : "");

export const amApi = {
  availability: () => call<Availability>("/availability"),
  catalog: () => call<{ agents: CatalogAgent[] | null }>("/catalog"),
  catalogItem: (slug: string) => call<CatalogItem>(`/catalog/${id(slug)}`),

  install: (body: { slug: string; idempotency_key: string; self_test: boolean; settings?: Record<string, unknown>; theme?: unknown }) =>
    send<InstallResponse>("POST", "/installs", body),
  installs: () => call<{ installs: InstallRow[] | null }>("/installs"),
  getInstall: (installId: string) => call<InstallDetail>(`/installs/${id(installId)}`),
  removeInstall: (installId: string) => send<{ removed: boolean }>("DELETE", `/installs/${id(installId)}`),

  /** AM6: declared = the settings the agent's manifest declares, each with its current value. */
  defaults: (installId: string) =>
    call<{ defaults: Default[] | null; declared?: DeclaredSetting[] | null }>(`/installs/${id(installId)}/defaults`),
  putDefault: (installId: string, key: string, value: unknown, label?: string) =>
    send<{ saved: boolean }>("PUT", `/installs/${id(installId)}/defaults/${id(key)}`, label ? { value, label } : { value }),
  deleteDefault: (installId: string, key: string) =>
    send<{ removed: boolean }>("DELETE", `/installs/${id(installId)}/defaults/${id(key)}`),

  selfTest: (installId: string) => send<{ run: Run }>("POST", `/installs/${id(installId)}/self-test`),
  runs: (installId: string) => call<{ runs: Run[] | null }>(`/installs/${id(installId)}/runs`),
  thread: (installId: string, after = 0, thread?: string | null) =>
    call<{ items: ThreadItem[] | null; cursor: number }>(`/installs/${id(installId)}/thread?after=${after}${threadQ(thread)}`),
  /** The raw SSE response; the caller parses it (lib/stream parseSSE). */
  events: (installId: string, after: number, signal: AbortSignal, thread?: string | null) =>
    api.raw(`${BASE}/installs/${id(installId)}/events?after=${after}${threadQ(thread)}`, withLang({ signal })),

  upload: (installId: string, file: File, opts: UploadOptions = {}) =>
    uploadOnce(`${BASE}/installs/${id(installId)}/files`, file, opts, false, (b) => (b as { file?: AmFile } | null)?.file),
  /** AM6: knowledge_base_id goes with a question only (no files): knowledge answers, it never feeds a deliverable. */
  message: (
    installId: string,
    body: { text: string; file_ids: string[]; client_id: string; thread_id?: string | null; knowledge_base_id?: string; form_file_ids?: string[] },
  ) =>
    send<{ item: ThreadItem; route?: string; run_id?: string; duplicate?: boolean }>("POST", `/installs/${id(installId)}/messages`, body),

  mappingPreview: (installId: string, fileId: string) =>
    send<{ job: Run }>("POST", "/mapping-preview", { install_id: installId, file_id: fileId }),
  answerCard: (cardId: string, body: AnswerBody) =>
    send<{ changed: boolean; status?: string }>("POST", `/cards/${id(cardId)}/answer`, body),

  run: (runId: string) => call<RunDetail>(`/runs/${id(runId)}`),
  computed: (runId: string) => call<Computed>(`/runs/${id(runId)}/computed`),
  cancelRun: (runId: string) => send<{ run: Run }>("POST", `/runs/${id(runId)}/cancel`),
  /** AM5: a failed round, tried again with the files it had (a new round; its progress card comes back). */
  retry: (runId: string) => send<{ run: Run; item?: ThreadItem }>("POST", `/runs/${id(runId)}/retry`),
  /** AM5: a document the round left out, brought back; the same round computes again. */
  restore: (runId: string, docKey: string) =>
    send<{ changed: boolean; asks: boolean; doc_key: string; name: string }>("POST", `/runs/${id(runId)}/restore`, { doc_key: docKey }),
  formats: (runId: string, formats: string[]) => send<{ job: Run }>("POST", `/runs/${id(runId)}/formats`, { formats }),

  downloadArtifact: (artifactId: string, filename: string) => saveBlob(`/artifacts/${id(artifactId)}/download`, filename),
  downloadFile: (fileId: string, filename: string) => saveBlob(`/files/${id(fileId)}/download`, filename),

  /** The customer's own template: upload a (new version of the) file → an inspect job; save the confirmed mapping; stop using it. */
  uploadTemplate: (installId: string, file: File, opts: UploadOptions = {}) =>
    uploadOnce(`${BASE}/installs/${id(installId)}/template`, file, opts, false, (b) => {
      const r = b as TemplateUpload | null;
      return r?.file && r?.job ? r : undefined;
    }),
  saveTemplate: (installId: string, body: { file_id: string; slots: TemplateSlot[]; mapping: Record<string, string> }) =>
    send<{ template: SavedTemplate }>("PUT", `/installs/${id(installId)}/template`, body),
  removeTemplate: (installId: string) => send<{ removed: boolean }>("DELETE", `/installs/${id(installId)}/template`),

  /** AM12: the searches a run made and the addresses it asked for; a picture cut from a saved page (the
   *  evidence a candidate's price was read from) and a saved page's whole picture, as object URLs — they
   *  are the run owner's, fetched with the session like any call (revoke the URL when done). */
  runSources: (runId: string) => call<{ calls: WebCall[] | null }>(`/runs/${id(runId)}/sources`),
  evidencePicture: (runId: string, name: string) => blobUrl(`/runs/${id(runId)}/evidence/${encodeURIComponent(name)}`),
  sourcePicture: (runId: string, source: string) => blobUrl(`/runs/${id(runId)}/sources/${encodeURIComponent(source)}/picture`),

  /** AM2 phase F: what a run left for a person to decide, and a decision (one of the row's own choices). */
  runExceptions: (runId: string) => call<{ exceptions: RunExceptionRow[] | null }>(`/runs/${id(runId)}/exceptions`),
  decideException: (exceptionId: string, body: { choice: unknown; note?: string }) =>
    send<{ exception: RunExceptionRow }>("POST", `/exceptions/${id(exceptionId)}/decision`, body),

  /** AM2 phase G: versions (upgrade after a self-test of the new one; roll back) and the per-agent dashboard. */
  upgradeInfo: (installId: string) => call<UpgradeInfo>(`/installs/${id(installId)}/upgrade`),
  upgrade: (installId: string, definitionId: string) =>
    send<{ run: Run }>("POST", `/installs/${id(installId)}/upgrade`, { definition_id: definitionId }),
  rollback: (installId: string) => send<{ install: Install }>("POST", `/installs/${id(installId)}/rollback`),
  dashboard: (installId: string, days: number) => call<AgentDashboard>(`/installs/${id(installId)}/dashboard?days=${days}`),

  theme: () => call<ThemeResponse>("/theme"),
  putTheme: (theme: { id: string; company?: string }) => send<{ version: number }>("PUT", "/theme", theme),

  // ── AM3: self-serve ────────────────────────────────────────────────────

  /** The left rail: my installs with pins, recent use and what waits for an answer. */
  rail: () => call<RailResponse>("/rail"),
  putPrefs: (prefs: RailPrefs) => send<{ prefs: RailPrefs }>("PUT", "/prefs", prefs),
  pin: (installId: string) => send<{ pinned_at: string | null }>("PUT", `/installs/${id(installId)}/pin`),
  unpin: (installId: string) => send<{ pinned_at: string | null }>("DELETE", `/installs/${id(installId)}/pin`),
  opened: (installId: string) => send<{ ok: boolean }>("POST", `/installs/${id(installId)}/opened`),

  /** The three monthly limits of an install (the workspace banner). */
  budget: (installId: string) => call<{ budget: BudgetState; remaining: number }>(`/installs/${id(installId)}/budget`),
  /** "ลองต่อ" on a run waiting for budget. */
  resume: (runId: string) => send<{ resumed: boolean }>("POST", `/runs/${id(runId)}/resume`),

  /** The catalog a stranger reads: real sample outputs and the data sheet. */
  catalogSample: (slug: string) => call<CatalogSample>(`/catalog/${id(slug)}/sample`),
  datasheet: (slug: string) => call<Datasheet>(`/catalog/${id(slug)}/datasheet`),
  downloadSampleArtifact: (artifactId: string, filename: string) => saveBlob(`/sample-artifacts/${id(artifactId)}/download`, filename),
  downloadSampleFile: (fileId: string, filename: string) => saveBlob(`/sample-files/${id(fileId)}/download`, filename),

  /** Sign-off: a person's name on a finished run and its files. */
  review: (runId: string, body: { name?: string; note?: string }) => send<{ review: Review; job: Run }>("POST", `/runs/${id(runId)}/review`, body),
  reviews: (runId: string) => call<{ reviews: Review[] | null }>(`/runs/${id(runId)}/reviews`),

  /** Sharing a run with a named colleague in the organisation (sign-in required). */
  shares: (runId: string) => call<{ shares: Share[] | null }>(`/runs/${id(runId)}/shares`),
  share: (runId: string, body: { email: string; can_review: boolean; expires_days?: number }) =>
    send<{ share: Share; path: string }>("POST", `/runs/${id(runId)}/shares`, body),
  revokeShare: (shareId: string) => send<{ revoked: boolean }>("DELETE", `/shares/${id(shareId)}`),
  people: (q: string) => call<{ people: Person[] | null }>(`/people?q=${encodeURIComponent(q)}`),
  sharedWithMe: () => call<{ shared: SharedItem[] | null }>("/shared"),
  sharedRun: (shareId: string) => call<SharedRun>(`/shared/${id(shareId)}`),
  downloadShared: (shareId: string, artifactId: string, filename: string) =>
    saveBlob(`/shared/${id(shareId)}/artifacts/${id(artifactId)}/download`, filename),
  sharedReview: (shareId: string, body: { name?: string; note?: string }) =>
    send<{ review: Review; job: Run }>("POST", `/shared/${id(shareId)}/review`, body),
  sharedProblem: (shareId: string, body: { text: string; page?: string }) =>
    send<{ report: ProblemReport; message: string }>("POST", `/shared/${id(shareId)}/problem`, body),

  /** "Report a problem": one sentence, the run and the page come along. */
  runProblem: (runId: string, body: { text: string; page?: string }) =>
    send<{ report: ProblemReport; message: string }>("POST", `/runs/${id(runId)}/problem`, body),
  installProblem: (installId: string, body: { text: string; page?: string }) =>
    send<{ report: ProblemReport; message: string }>("POST", `/installs/${id(installId)}/problem`, body),


  // ── AM4: rounds, threads, the agent's model ─────────────────────────────

  threads: (installId: string) => call<{ threads: AgentThread[] | null }>(`/installs/${id(installId)}/threads`),
  /** AM11b: when an empty chat is already there the server answers with it (`reused`) instead of making another. */
  newThread: (installId: string) => send<{ thread: AgentThread; reused?: boolean }>("POST", `/installs/${id(installId)}/threads`),
  /** AM10: the form a person dropped into a round, and the mapping they confirm for it (that round only). */
  runForm: (runId: string) => call<RunFormDetail>(`/runs/${id(runId)}/template`),
  saveRunForm: (runId: string, body: { slots: TemplateSlot[]; mapping: Record<string, string> }) =>
    send<{ form: RunFormDetail["form"]; job: Run | null }>("PUT", `/runs/${id(runId)}/template`, body),
  /** AM10: rename ({title}, "" = the derived name again) or pin ({pinned}) a chat; "main" is the first. */
  patchThread: (installId: string, threadKey: string, body: { title?: string; pinned?: boolean }) =>
    send<{ thread: AgentThread }>("PATCH", `/installs/${id(installId)}/threads/${id(threadKey)}`, body),
  /** AM10: take a chat off the list; its rounds' evidence stays. */
  deleteThread: (installId: string, threadKey: string) =>
    send<{ deleted: boolean; cancelled_runs: number; kept_runs: number }>("DELETE", `/installs/${id(installId)}/threads/${id(threadKey)}`),
  /** "เริ่มรอบใหม่": the next files (or instruction) in this thread start the next round. */
  startRound: (installId: string, threadId?: string | null) =>
    send<{ item: ThreadItem }>("POST", `/installs/${id(installId)}/rounds`, { thread_id: threadId ?? null }),
  /** The round-boundary chip's answer. */
  answerIntent: (itemId: string, choice: "new_round" | "about_last") =>
    send<{ changed: boolean; choice?: string; run_id?: string }>("POST", `/intents/${id(itemId)}`, { choice }),
  models: (installId: string) => call<AgentModels>(`/installs/${id(installId)}/models`),
  putModel: (installId: string, choice: string) => send<AgentModels>("PUT", `/installs/${id(installId)}/model`, { choice }),
  storage: (installId: string) => call<StorageInfo>(`/installs/${id(installId)}/storage`),


  // ── AM6: the cost before a round, a receipt, reminders, notifications, e-mail ──

  /** What a round of this agent usually costs, said before it starts. */
  estimate: (installId: string) => call<Estimate>(`/installs/${id(installId)}/estimate`),
  /** A receipt a person confirms here ("all" or a quantity, as text); the same round computes again. */
  confirmReceipt: (runId: string, key: string, quantity: string) =>
    send<{ run: Run }>("POST", `/runs/${id(runId)}/receipts`, { key, quantity }),

  reminders: (installId: string) => call<RemindersResponse>(`/installs/${id(installId)}/reminders`),
  addReminder: (installId: string, body: { label: string; rule: ReminderRule }) =>
    send<{ reminder: Reminder }>("POST", `/installs/${id(installId)}/reminders`, body),
  snoozeReminder: (reminderId: string, days: 1 | 3 | 7) =>
    send<{ reminder: Reminder }>("POST", `/reminders/${id(reminderId)}/snooze`, { days }),
  skipReminder: (reminderId: string) => send<{ reminder: Reminder }>("POST", `/reminders/${id(reminderId)}/skip`),
  stopReminder: (reminderId: string) => send<{ reminder: Reminder }>("POST", `/reminders/${id(reminderId)}/stop`),
  resumeReminder: (reminderId: string) => send<{ reminder: Reminder }>("POST", `/reminders/${id(reminderId)}/resume`),
  deleteReminder: (reminderId: string) => send<null>("DELETE", `/reminders/${id(reminderId)}`),

  /** What this surface has not shown yet, newest first. */
  notifications: (surface: "web" | "desktop") => call<{ notifications: AmNotification[] | null }>(`/notifications?surface=${surface}`),
  notificationShown: (notificationId: string, surface: "web" | "desktop") =>
    send<null>("POST", `/notifications/${id(notificationId)}/shown`, { surface }),
  dismissNotification: (notificationId: string) => send<null>("POST", `/notifications/${id(notificationId)}/dismiss`),

  /** A drafted e-mail about a finished round (also a thread item of kind email_draft). */
  draftEmail: (runId: string) => send<{ draft: EmailDraft }>("POST", `/runs/${id(runId)}/email-draft`),
  emailDraft: (draftId: string) => call<{ draft: EmailDraft }>(`/email-drafts/${id(draftId)}`),
  /** Sent only with the person's approval of this very text (its hash) and the recipients they typed. */
  sendEmail: (draftId: string, body: { to: string[]; cc: string[]; body_sha256: string; approve: true }) =>
    // AM7: message = why the organisation's relay did not take it (a failed send only)
    send<{ send: EmailSend; message?: string }>("POST", `/email-drafts/${id(draftId)}/send`, body),
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
