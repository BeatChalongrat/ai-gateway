// Host-side tools: capabilities that need something
// only the host has — the user's session, the agent key, Electron's
// clipboard, or the orchestrator itself. Same names, descriptions, schemas,
// categories and default permissions as the WPF tools they replace, so the
// model's tool list and the consent classifier see no difference.

import { OFFICE_MEDIA_TOOLS, officeMediaTool } from "./office-media-tools";
import { clipboard } from "electron";
import type { ChatEdgeClient } from "./chat-edge-client";
import { explicitRememberRequest } from "./explicit-remember";
import { memoryLabel } from "./prompt-assembler";
import type { GatewayClient } from "./gateway-client";
import type { Logger } from "./logger";
import { transferLocalFile } from "./local-office-files";

export interface HostToolDefinition {
  name: string;
  description: string;
  inputSchema: unknown;
}
export interface HostToolResult {
  ok: boolean;
  text: string;
  imageDataUri: string | null;
  /** FIX79 — EVERY image this one call produced, when there is more than
   *  one (run_python alternatives saved under distinct names). The card
   *  renders them all; imageDataUri stays the first for the paths that
   *  can only carry one (the model's vision message). */
  imageDataUris?: string[];
  /** FIX80 — ConversationFiles this call persisted server-side (run_python
   *  artifacts: charts AND xlsx/csv/…). The coordinator shows them under
   *  the assistant message (message_files) and links them at import
   *  (file_ids), so the user can open and download them from the chat. */
  attachedFiles?: { id: string; filename: string; content_type: string; file_size_bytes: number; extraction_status?: string }[];
  /** FIX130 — `remember` saved a memory: what the page's "บันทึกความจำแล้ว" /
   *  "เสนอความจำใหม่ — รอยืนยัน" marker shows (the `memory_saved` turn event). */
  memorySaved?: { id: string; label: string; status: "active" | "pending_confirmation" };
}

/** FIX130 — the context a host tool runs in. `userMessage` is the text the
 *  USER sent this turn (for a sub-agent: its parent turn's, never the task the
 *  model wrote); `remember` keeps an active save only when it asks to remember. */
export interface HostToolContext {
  conversationId: string;
  toolCallId: string;
  signal: AbortSignal;
  memoryEnabled?: boolean;
  userMessage?: string;
  /** Host-owned snapshot; never accept folder grants from model arguments. */
  grantedFolders?: readonly string[];
  forbiddenFolders?: readonly string[];
}

/** FIX130 — how much of the user's message rides a suggestion as its evidence. */
const EVIDENCE_MAX_CHARS = 200;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export interface NestedRunRequest {
  task: string;
  /** Allow-list of tool names, or null = every tool except run_subagent. */
  tools: string[] | null;
  /** FIX31 §1.1: the sub-agent's name on a consent card ("sub-agent “date”").
   *  Fan-out passes the task label; a single task has none. */
  label?: string;
  parentToolCallId: string;
  conversationId: string;
  /** Inherited from the chat that spawned this sub-agent. A sub-agent of a
   *  memory-off chat must not save memory either — otherwise the switch is
   *  escapable by asking for the work to be delegated. */
  memoryEnabled?: boolean;
  /** FIX130 — the PARENT turn's user message. The task text is written by
   *  the model, so it can never be what makes a `remember` explicit. */
  userMessage?: string;
}
/** FIX30 §3.1: a sub-agent's final answer that opens like this is a failure
 *  (the prompt asks for `FAILED:`; `ERROR:` is what a model writes unprompted). */
export const SUBAGENT_FAILED_RE = /^\s*(?:FAILED|ERROR)\s*:/i;

export interface NestedRunResult {
  answer: string;
  completed: boolean;
  stopReason: string | null;
  trace: string[];
}

export const HOST_TOOL_DEFINITIONS: Record<string, HostToolDefinition> = {
  ...OFFICE_MEDIA_TOOLS,
  import_local_file: {
    name: "import_local_file",
    description: "Upload a copy of an Office document, PDF, text/data file or image from a granted local folder into this conversation (up to 25 MB). This sends the file to the server. Returns a file_id for run_python.files and attaches it to the chat. Use an absolute path; symbolic links are refused. The original remains unchanged.",
    inputSchema: { type: "object", properties: { path: { type: "string", description: "Absolute path inside a folder granted in Desktop Settings." } }, required: ["path"] },
  },
  save_conversation_file: {
    name: "save_conversation_file",
    description: "Save an attached or generated file from this conversation to a NEW file in a granted local folder (up to 25 MB). Provide its file_id and absolute destination path. The parent folder must exist. Existing files are never overwritten; choose a new filename for an edited copy.",
    inputSchema: { type: "object", properties: { file_id: { type: "string" }, path: { type: "string", description: "Absolute destination path with filename, inside a granted folder." } }, required: ["file_id", "path"] },
  },
  read_clipboard: {
    name: "read_clipboard",
    description: "Read the current text content of the system clipboard.",
    inputSchema: { type: "object", properties: {} },
  },
  write_clipboard: {
    name: "write_clipboard",
    description: "Replace the system clipboard content with the given text.",
    inputSchema: { type: "object", properties: { text: { type: "string", description: "Text to place on the clipboard" } }, required: ["text"] },
  },
  web_search: {
    name: "web_search",
    description:
      "Search the web for current information. Use when asked about recent events, current data, prices, news, or anything that may have changed since training.",
    inputSchema: { type: "object", properties: { query: { type: "string", description: "Search query" } }, required: ["query"] },
  },
  // FIX9 Part B: image generation, host-side via the gateway (the run_python /
  // web_search precedent — the sidecar never holds the JWT). Offered only when the
  // user turns on image mode (the 🎨 button); the model calls it to make an image.
  generate_image: {
    name: "generate_image",
    description:
      "Generate or edit an image and attach it to your reply. Preserve literal text, original language and user constraints. To edit, supply source_file_ids from this conversation: those bytes are sent to the configured image provider. Only use references the user asked to use.",
    inputSchema: { type: "object", properties: {
      prompt: { type: "string", description: "Description or edit instruction. Preserve quoted text exactly." },
      source_file_ids: { type: "array", maxItems: 4, items: { type: "string" }, description: "Existing conversation image IDs; 4 MiB each / 12 MiB total." },
      size: { type: "string", enum: ["1024x1024", "1024x1536", "1536x1024"] },
      quality: { type: "string", enum: ["auto", "low", "medium", "high"] },
    }, required: ["prompt"] },
  },
  run_python: {
    name: "run_python",
    description:
      "Execute Python in a secure, isolated server sandbox (no network) and return its stdout/stderr. " +
      "Use it for real computation, data analysis, math, or to generate a chart or a file — instead of " +
      "guessing. Available: data science (numpy, pandas, scipy, scikit-learn, statsmodels, sympy), " +
      "charts (matplotlib, seaborn — use matplotlib.use('Agg'); Thai text renders correctly by " +
      "default), reading AND writing Excel, Word, PowerPoint and PDF files, OCR of scanned documents " +
      "including Thai, images (pillow), and legacy Thai-encoded CSVs (charset-normalizer detects " +
      "TIS-620/CP874). " +
      "print() what you want to see. THE way to hand the user a picture or a file (PNG, xlsx, csv, …) " +
      "is to WRITE IT TO /job/out/ — e.g. plt.savefig('/job/out/chart.png') or " +
      "df.to_excel('/job/out/report.xlsx'). Everything in /job/out/ is attached to the chat, where the " +
      "user can view and download it; files written anywhere else are discarded. Files made with " +
      "run_command or write_file stay on the user's computer; use import_local_file to attach supported files. " +
      "Use save_conversation_file to save a generated file into a granted local folder. " +
      "pasting base64 of a file into your reply never works — use /job/out/ instead. " +
      "To read or edit an attached or previously generated file, pass its file_id in files. " +
      "Each input is mounted read-only at /job/in/<original_filename>; save the edited copy to /job/out/. " +
      "Read the output, then refine and run again or explain the result. Do NOT invent output you did " +
      "not see. Making alternatives for the user to choose between? Save them all in ONE run under " +
      "distinct names (/job/out/option-a.png, /job/out/option-b.png) — all are shown. Re-running " +
      "because the previous attempt was wrong? Write to the SAME filename — only the last attempt " +
      "stays visible. " +
      "For Office templates: import sys; sys.path.insert(0, '/sandbox/scripts'); " +
      "from office_templates import catalog, create, inspect_file. Print catalog() first for schemas. " +
      "For animated business graphics: import motion_templates; print(motion_templates.catalog()); " +
      "motion_templates.render('motion.explainer' (or motion.metrics/motion.slideshow), data, '/job/out/brief.mp4'). " +
      "It exports silent MP4, poster, contact sheet and editable storyboard JSON; review these before delivery. " +
      "create(template_id, data, '/job/out/name.ext') supports pptx executive_brief/project_update/proposal, " +
      "docx business_report/proposal/meeting_minutes, pdf business_report/invoice, xlsx budget/tracker/analysis " +
      "and csv data_export (IDs use format.name). Supply real user facts, typed columns and sources; " +
      "never invent missing figures. Inspect original files before editing; preserve their templates. " +
      "Read the Office quality report: checked means rendered, not visually approved. Fix formula errors; " +
      "do not claim recalculated values when recalculated=false. Inspect attached previews before delivery.",
    inputSchema: {
      type: "object",
      properties: {
        code: { type: "string", description: "The Python source to run. Self-contained; use print() to surface results." },
        files: { type: "array", items: { type: "string" }, description: "File IDs from this conversation to mount read-only at /job/in/<original_filename>." },
      },
      required: ["code"],
    },
  },
  // FIX11 §2: search the user's own past conversations/messages/memories.
  // Host-side (chat-edge GET /api/v1/search) because it needs the user's
  // session — the run_python / web_search precedent; the sidecar never holds
  // the JWT. chat-edge scopes every query to the authenticated (user, tenant),
  // so a session can only ever search ITS OWN history.
  search_past_conversations: {
    name: "search_past_conversations",
    description:
      "Search the user's OWN earlier conversations, messages and saved memories for relevant context. " +
      "Use it when the user refers to something discussed before — in this or another chat — or asks what " +
      "you talked about earlier. Returns matching snippets, scoped to this user only.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Keywords to search for." },
        scope: { type: "string", enum: ["all", "messages", "conversations", "memories"], description: "What to search. Default 'all'." },
        max_results: { type: "integer", description: "How many results to return (1-20, default 10)." },
      },
      required: ["query"],
    },
  },
  // FIX58 — `remember` is a HOST tool for the global scope only.
  //
  // Before this it wrote the sidecar's local SQLite notes, so a fact
  // saved on the desktop was invisible on the web: two clients, two
  // stores, one user wondering why the assistant forgot. The host owns
  // the portal JWT (the run_python / search_past_conversations
  // precedent), so the save goes to chat.user_memories — the store the
  // web client already writes and the Memory page already shows.
  //
  // The schema is kept identical to the sidecar's so the model's tool
  // description does not change and existing transcripts stay valid;
  // when the sidecar is up its definition is the one offered, and only
  // the EXECUTION is intercepted (turn-coordinator routes by
  // hostTools.has). Listing it here also means `remember` survives a
  // sidecar that failed to start.
  //
  // scope="conversation" is deliberately NOT taken over: those notes
  // are read back from the local store by the prompt assembler, and
  // the server's injection query reads global/project scopes only — so
  // routing them here would save them somewhere nothing reads them
  // back from, which is worse than the split it was meant to fix.
  remember: {
    name: "remember",
    description:
      "Save a durable note. scope 'global' stores a lasting fact about the user (available in every later chat, " +
      "on the web app too); scope 'conversation' stores a detail that only matters inside this chat. " +
      "Use it when the user asks you to remember something, or states a standing preference.",
    inputSchema: {
      type: "object",
      properties: {
        note: { type: "string", description: "The fact to remember, written as a complete sentence." },
        scope: { type: "string", enum: ["global", "conversation"], description: "'global' for a lasting user-level fact, 'conversation' for this chat only. Default 'global'." },
      },
      required: ["note"],
    },
  },
  run_subagent: {
    name: "run_subagent",
    description:
      "Delegate a self-contained task to a focused sub-agent that has the same tools but cannot see this conversation. " +
      "Use it for a bounded piece of work (research a question, inspect a folder, run and summarise a command) whose " +
      "details would clutter this conversation, or pass 'tasks' to run 2+ INDEPENDENT read-only subtasks in parallel. " +
      "Give it every detail it needs; it returns only its final answer.",
    inputSchema: {
      type: "object",
      properties: {
        task: { type: "string", description: "A single self-contained task. The sub-agent cannot see this conversation, so include all needed context (paths, names, constraints)." },
        tools: { type: "array", items: { type: "string" }, description: "Optional tool-name allowlist for a single-'task' sub-agent. Omit to allow every available tool. run_subagent itself is never available inside a sub-agent." },
        tasks: {
          type: "array",
          items: { type: "object", properties: { task: { type: "string", description: "Self-contained instructions for this subtask." }, label: { type: "string", description: "Short label for this subtask in the combined result." } }, required: ["task"] },
          description: "2+ INDEPENDENT subtasks to run in PARALLEL. Each has the same tools as a single sub-agent; a subtask's tool call asks the user for permission exactly as yours would, and the prompt names the subtask that asked. A subtask the user refuses fails (its result starts with FAILED:) — do not re-run it.",
        },
      },
    },
  },
};

export const NESTED_MAX_ITERATIONS = 25;
export const NESTED_MAX_MINUTES = 8;
export const MAX_FANOUT_TASKS = 6;
export const MAX_CONCURRENT_FANOUT = 3;
const MAX_TRACE_ENTRIES = 15;

export class HostTools {
  constructor(
    private readonly gateway: GatewayClient,
    private readonly chatEdge: ChatEdgeClient,
    private readonly runNested: (req: NestedRunRequest, signal: AbortSignal) => Promise<NestedRunResult>,
    private readonly log: Logger,
    private readonly webSearchEnabled: () => boolean,
  ) {}

  definitions(): HostToolDefinition[] {
    const all = Object.values(HOST_TOOL_DEFINITIONS);
    return all.filter((d) => d.name !== "web_search" || this.webSearchEnabled());
  }

  has(name: string): boolean {
    return name in HOST_TOOL_DEFINITIONS && (name !== "web_search" || this.webSearchEnabled());
  }

  async execute(name: string, args: Record<string, unknown>, ctx: HostToolContext): Promise<HostToolResult> {
    if (name in OFFICE_MEDIA_TOOLS) return officeMediaTool(name, args, ctx, this.chatEdge);
    switch (name) {
      case "import_local_file":
      case "save_conversation_file":
        return transferLocalFile(name, args, ctx, this.chatEdge);
      case "read_clipboard": {
        const text = String(await clipboard.readText());
        return { ok: true, text: text.length > 0 ? text : "Clipboard has no text content.", imageDataUri: null };
      }
      case "write_clipboard": {
        const text = args["text"];
        if (typeof text !== "string") return { ok: false, text: "'text' is required.", imageDataUri: null };
        clipboard.writeText(text);
        return { ok: true, text: `Clipboard set (${text.length} chars).`, imageDataUri: null };
      }
      case "remember": {
        // Only the global scope reaches here; conversation-scoped notes
        // stay with the sidecar (see the definition above), and the
        // turn coordinator routes on that.
        const note = args["note"];
        if (typeof note !== "string" || !note.trim()) {
          return { ok: false, text: "'note' is required.", imageDataUri: null };
        }
        // A chat that belongs to a project remembers INTO that project
        // (FIX58): its memories are read back only inside it, so a
        // global save here would put a fact learned on one client's
        // work into every other chat. The conversation is the only
        // place that knows, so it is asked per save — `remember` is
        // rare enough that the round-trip is not a hot path.
        let scopeType: "global_user" | "project" = "global_user";
        let scopeId: string | null = null;
        try {
          const conv = await this.chatEdge.getConversation(ctx.conversationId);
          if (conv?.project_id) {
            scopeType = "project";
            scopeId = conv.project_id;
          }
        } catch {
          // Could not tell — save globally rather than lose the note.
          // Widening the audience is the wrong way to fail, so this is
          // logged loudly enough to notice if it becomes common.
          this.log.warn(`remember: could not resolve the project for conversation ${ctx.conversationId}; saving to global memory`);
        }
        // FIX130 — the model's decision to call `remember` is not the user's
        // request to be remembered. On the test stack a model saved plain
        // self-descriptions ("my nickname is …, I work as …") nobody had asked
        // it to keep, as ACTIVE memories injected into every later chat; and
        // anything that can steer the model (a fetched page, a tool result)
        // could plant one the same way. The save stays active only when the
        // user's own message this turn asks to remember (the matcher shared
        // with chat-edge); otherwise it becomes a pending suggestion — the
        // same review queue the background extractor fills — with this chat
        // named as its source and the user's words as its evidence.
        const explicit = explicitRememberRequest(ctx.userMessage);
        // Either way the row names the chat it came from, so the Memory page
        // links back to it instead of calling it "added by hand".
        const source = UUID_RE.test(ctx.conversationId) ? { sourceConversationId: ctx.conversationId } : {};
        const suggestion = explicit
          ? source
          : {
              sourceType: "assistant_inferred" as const,
              ...source,
              ...(ctx.userMessage && ctx.userMessage.trim() ? { evidenceExcerpt: Array.from(ctx.userMessage.trim()).slice(0, EVIDENCE_MAX_CHARS).join("") } : {}),
            };
        const r = await this.chatEdge.createMemory({ content: note.trim(), scopeType, scopeId, ...suggestion });
        if (r.ok) {
          const status: "active" | "pending_confirmation" = r.status === "pending_confirmation" || (!r.status && !explicit) ? "pending_confirmation" : "active";
          const memorySaved = { id: r.id, label: memoryLabel(r.content), status };
          if (status === "pending_confirmation") {
            return {
              ok: true,
              text: `Noted "${r.content}" — queued for the user's review on the Memory page (not active yet; it will be used once they confirm it). Say so plainly if you mention it; do not claim it is saved.`,
              imageDataUri: null,
              memorySaved,
            };
          }
          const where = scopeType === "project" ? "this project's memory" : "your memory";
          return { ok: true, text: `Saved to ${where}: "${r.content}"`, imageDataUri: null, memorySaved };
        }
        // Never report a save that did not happen (FIX5 §1 / FIX22 §1a).
        // The server's own sentence is passed through when it has one —
        // it names the actual reason (guardrail, duplicate, masked
        // content) far better than a generic failure could.
        if (r.status === 0) {
          return {
            ok: false,
            text: "Error: the note was NOT saved — the memory service could not be reached. Tell the user plainly that it was not saved and they can ask again once the connection is back.",
            imageDataUri: null,
          };
        }
        return { ok: false, text: `Error: the note was NOT saved. ${r.detail || `The memory service returned HTTP ${r.status}.`}`, imageDataUri: null };
      }
      case "web_search": {
        const query = args["query"];
        if (typeof query !== "string" || !query.trim()) return { ok: false, text: "'query' is required.", imageDataUri: null };
        const r = await this.gateway.search(query.trim(), 5, ctx.signal);
        if (!r.ok) return { ok: false, text: r.error, imageDataUri: null };
        const items = r.results.map((x) => ({ title: x.title, url: x.url, snippet: x.snippet ?? "" }));
        return { ok: true, text: JSON.stringify({ query, results: items }), imageDataUri: null };
      }
      case "generate_image": {
        const prompt = args["prompt"];
        if (typeof prompt !== "string" || !prompt.trim()) return { ok: false, text: "'prompt' is required.", imageDataUri: null };
        let sources: string[];
        try {
          const { imageSources } = await import("./image-sources");
          sources = await imageSources(this.chatEdge, ctx.conversationId, args["source_file_ids"], ctx.signal);
        } catch (err) { return { ok: false, text: (err as Error).message, imageDataUri: null }; }
        const size = args["size"];
        const quality = args["quality"];
        if (size !== undefined && !["1024x1024", "1024x1536", "1536x1024"].includes(String(size))) return { ok: false, text: "Unsupported image size.", imageDataUri: null };
        if (quality !== undefined && !["auto", "low", "medium", "high"].includes(String(quality))) return { ok: false, text: "Unsupported image quality.", imageDataUri: null };
        const r = await this.gateway.generateImage(prompt.trim(), ctx.signal, {
          ...(sources.length ? { source_images: sources } : {}),
          ...(typeof size === "string" ? { size } : {}),
          ...(typeof quality === "string" ? { quality } : {}),
        });
        if (!r.ok) return { ok: false, text: r.error, imageDataUri: null };
        // FIX67 — tell the user which model produced it (every time). After a
        // fallback this is the second-tier model, not the primary.
        const madeBy = r.producedBy ? ` It was made by ${r.producedBy}; mention that to the user.` : "";
        // FIX81 ① — persist the image as a ConversationFile (the FIX80
        // delivery pipeline), so it survives a restart, renders as an
        // attachment and downloads — instead of living only in this
        // session's data URI. A failed save degrades to the old card-only
        // behaviour; the turn is never lost over it.
        const comma = r.dataUri.indexOf(",");
        const b64 = comma >= 0 ? r.dataUri.slice(comma + 1) : "";
        const name = imageFilenameFor(prompt.trim());
        const saved = b64 ? await this.chatEdge.attachGeneratedFile(ctx.conversationId, b64, name, guessImageMime(r.dataUri), r.producedBy ?? undefined) : null;
        return {
          ok: true,
          text: `Generated an image${r.revised ? ` (revised prompt: ${r.revised})` : ""}.${madeBy}${saved ? ` It is attached to this chat; file_id=${saved.id} (reusable for edits).` : ""} The image follows in the next message.`,
          imageDataUri: r.dataUri,
          ...(saved ? { attachedFiles: [{ id: saved.id, filename: saved.filename, content_type: saved.content_type, file_size_bytes: saved.file_size_bytes }] } : {}),
        };
      }
      case "run_python": {
        const code = args["code"];
        if (typeof code !== "string" || !code.trim()) return { ok: false, text: "'code' is required.", imageDataUri: null };
        const rawFiles = args["files"] ?? [];
        if (!Array.isArray(rawFiles) || rawFiles.some((id) => typeof id !== "string" || !UUID_RE.test(id.trim()))) {
          return { ok: false, text: "'files' must be an array of file IDs from this conversation.", imageDataUri: null };
        }
        const fileIds = [...new Set(rawFiles.map((id: string) => id.trim()))];
        // FIX80 — naming the conversation makes chat-edge PERSIST every
        // /job/out/ artifact as a ConversationFile; before this, images
        // lived only in a session data URI and .xlsx/.csv bytes were
        // silently discarded, so the model went hunting for somewhere on
        // the user's disk to write a "real file" (the owner's PNG turn).
        const r = await this.chatEdge.runCode(code, fileIds, ctx.signal, ctx.conversationId);
        if (!r) return { ok: false, text: "Code interpreter call failed (chat-edge unreachable or refused).", imageDataUri: null };
        const lines: string[] = [`Exit code: ${r.exit}`];
        const stdout = (r.stdout ?? "").replace(/\n+$/, "");
        const stderr = (r.stderr ?? "").replace(/\n+$/, "");
        if (stdout) lines.push("", "stdout:", stdout);
        if (stderr && (r.exit !== 0 || !stdout)) lines.push("", "stderr:", stderr);
        if (r.truncated) lines.push("[output truncated]");
        if (r.timed_out) lines.push("The code timed out and was stopped.");
        if (!stdout && !stderr) lines.push("(no output was printed)");
        const images = r.images ?? [];
        const files = r.files ?? [];
        const attached: { id: string; filename: string; content_type: string; file_size_bytes: number }[] = [];
        for (const i of images) if (i.id) attached.push({ id: i.id, filename: i.filename, content_type: guessImageMime(i.data_uri), file_size_bytes: dataUriBytes(i.data_uri) });
        for (const f of files) if (f.id) attached.push({ id: f.id, filename: f.filename, content_type: f.content_type, file_size_bytes: f.size_bytes });
        if (attached.length) {
          lines.push("", "Attached to this chat (the user can open and download them there — no need to save them anywhere else): " + attached.map((a) => a.filename).join(", "));
          lines.push("To read or revise these files in another run_python call, use these file IDs:", JSON.stringify(attached.map((a) => ({ file_id: a.id, filename: a.filename }))));
        } else if (images.length || files.length) {
          lines.push("", "Generated: " + [...images.map((i) => i.filename), ...files.map((f) => f.filename)].join(", "));
        }
        // FIX79 — a single run may deliberately produce ALTERNATIVES; every
        // image rides the result so the card shows them all (the first one
        // used to be the only survivor).
        return {
          ok: r.exit === 0 && !r.timed_out,
          text: lines.join("\n"),
          imageDataUri: images[0]?.data_uri ?? null,
          ...(images.length > 1 ? { imageDataUris: images.map((i) => i.data_uri) } : {}),
          ...(attached.length ? { attachedFiles: attached } : {}),
        };
      }
      case "search_past_conversations": {
        const query = args["query"];
        if (typeof query !== "string" || !query.trim()) return { ok: false, text: "'query' is required.", imageDataUri: null };
        const scopeRaw = typeof args["scope"] === "string" ? String(args["scope"]) : "all";
        const scope = ["all", "messages", "conversations", "memories"].includes(scopeRaw) ? scopeRaw : "all";
        const maxRaw = typeof args["max_results"] === "number" ? Math.floor(args["max_results"] as number) : 10;
        const limit = Math.max(1, Math.min(20, Number.isFinite(maxRaw) && maxRaw > 0 ? maxRaw : 10));
        // FIX130 A2 — recalling past chats and saved memories is a memory
        // read: a chat whose memory is not active is not offered this tool
        // (withholdMemoryTools) and a call that arrives anyway reads nothing.
        // Same rule as chat-edge's own copy of this tool.
        if (ctx.memoryEnabled === false) {
          return { ok: false, text: "REFUSED: memory is off in this chat (switched off, or it is a temporary chat), so past chats and saved memories cannot be searched here. Answer from this conversation only.", imageDataUri: null };
        }
        const r = await this.chatEdge.searchPastConversations(query.trim(), scope, limit);
        if (!r) return { ok: false, text: "Past-conversation search is unavailable right now (chat-edge unreachable or refused).", imageDataUri: null };
        const results = Array.isArray(r.results) ? r.results : [];
        return { ok: true, text: JSON.stringify({ query: r.query ?? query.trim(), scope: r.scope ?? scope, total: r.total ?? results.length, results }), imageDataUri: null };
      }
      case "run_subagent": {
        const tasks = args["tasks"];
        if (Array.isArray(tasks) && tasks.length >= 2) {
          if (tasks.length > MAX_FANOUT_TASKS) return { ok: false, text: `At most ${MAX_FANOUT_TASKS} parallel subtasks.`, imageDataUri: null };
          const results: string[] = [];
          let next = 0;
          const workers = Array.from({ length: Math.min(MAX_CONCURRENT_FANOUT, tasks.length) }, async () => {
            while (next < tasks.length) {
              const i = next++;
              const t = tasks[i] as { task?: string; label?: string };
              const label = t.label?.trim() || `Task ${i + 1}`;
              // FIX31 §1.1: no more "auto-approvable tools only" for fan-out. A
              // subtask has every tool a single sub-agent has; a call that needs
              // permission raises the SAME consent card as the parent's would,
              // attributed to this subtask by label (the FIX8 boundary was a
              // stand-in for a consent path that did not exist; now it does).
              const r = await this.runNested({ task: String(t.task ?? ""), tools: null, label, parentToolCallId: ctx.toolCallId, conversationId: ctx.conversationId, memoryEnabled: ctx.memoryEnabled, userMessage: ctx.userMessage }, ctx.signal);
              results[i] = `## ${label}\n${r.completed ? r.answer : r.stopReason === "failed" ? `(failed)\n${r.answer}` : `(did not finish: ${r.stopReason ?? "unknown"})\n${r.answer}`}${trace(r.trace)}`;
            }
          });
          await Promise.all(workers);
          return { ok: true, text: results.join("\n\n"), imageDataUri: null };
        }
        const task = args["task"];
        if (typeof task !== "string" || !task.trim()) return { ok: false, text: "'task' (or 2+ 'tasks') is required.", imageDataUri: null };
        // An empty allow-list means "every tool" (SpawnAgentTool.ReadAllowlist), not "no tools".
        const allowRaw = Array.isArray(args["tools"]) ? (args["tools"] as unknown[]).filter((x): x is string => typeof x === "string") : [];
        const allow = allowRaw.length > 0 ? allowRaw : null;
        const r = await this.runNested({ task, tools: allow, parentToolCallId: ctx.toolCallId, conversationId: ctx.conversationId, memoryEnabled: ctx.memoryEnabled, userMessage: ctx.userMessage }, ctx.signal);
        if (r.completed) return { ok: true, text: r.answer + trace(r.trace), imageDataUri: null };
        if (r.stopReason === "failed") return { ok: false, text: `Sub-agent failed: ${r.answer}${trace(r.trace)}`, imageDataUri: null };
        return { ok: false, text: `Sub-agent did not finish: ${r.stopReason ?? "the run ended unexpectedly."}${trace(r.trace)}`, imageDataUri: null };
      }
      default:
        return { ok: false, text: `Unknown host tool '${name}'.`, imageDataUri: null };
    }
  }
}

/** Display filename for a generated image: a rune-safe slug of the
 *  prompt + .png (the web side's generatedImageFilename, in spirit). */
function imageFilenameFor(prompt: string): string {
  const slug = prompt
    .replace(/[\\/:*?"<>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40)
    .trim()
    .replace(/ /g, "-");
  return (slug || "generated-image") + ".png";
}

/** Mime from a data URI ("data:image/png;base64,…"), image/png fallback. */
function guessImageMime(dataUri: string): string {
  const m = /^data:([^;,]+)[;,]/.exec(dataUri);
  return m?.[1] ?? "image/png";
}

/** Approximate byte size of a base64 data URI's payload. */
function dataUriBytes(dataUri: string): number {
  const i = dataUri.indexOf(",");
  if (i < 0) return 0;
  return Math.floor(((dataUri.length - i - 1) * 3) / 4);
}

function trace(entries: string[]): string {
  if (entries.length === 0) return "";
  const shown = entries.slice(0, MAX_TRACE_ENTRIES).join(", ");
  return `\n\n[Sub-agent tool calls: ${shown}${entries.length > MAX_TRACE_ENTRIES ? `, … ${entries.length - MAX_TRACE_ENTRIES} more` : ""}]`;
}
