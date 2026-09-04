// Host-side tools (E4 §1 #1, #5, #7): the five tools that need something
// only the host has — the user's session, the agent key, Electron's
// clipboard, or the orchestrator itself. Same names, descriptions, schemas,
// categories and default permissions as the WPF tools they replace, so the
// model's tool list and the consent classifier see no difference.

import { clipboard } from "electron";
import type { ChatEdgeClient } from "./chat-edge-client";
import type { GatewayClient } from "./gateway-client";
import type { Logger } from "./logger";

export interface HostToolDefinition {
  name: string;
  description: string;
  inputSchema: unknown;
}
export interface HostToolResult {
  ok: boolean;
  text: string;
  imageDataUri: string | null;
}
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
      "Generate an image from a text description and attach it to your reply. Use it when the user asks you to draw, create, or generate a picture/image/logo/illustration.",
    inputSchema: { type: "object", properties: { prompt: { type: "string", description: "A detailed description of the image to generate." } }, required: ["prompt"] },
  },
  run_python: {
    name: "run_python",
    description:
      "Execute Python in a secure, isolated server sandbox (no network) and return its stdout/stderr. " +
      "Use it for real computation, data analysis, math, or to generate a chart — instead of guessing. " +
      "numpy, pandas, matplotlib and openpyxl are available (use matplotlib.use('Agg')). print() what " +
      "you want to see. To attach a chart image to your reply, save it to /job/out/ " +
      "(e.g. plt.savefig('/job/out/chart.png')). Read the output, then refine and run again or explain " +
      "the result. Do NOT invent output you did not see.",
    inputSchema: { type: "object", properties: { code: { type: "string", description: "The Python source to run. Self-contained; use print() to surface results." } }, required: ["code"] },
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

  async execute(name: string, args: Record<string, unknown>, ctx: { conversationId: string; toolCallId: string; signal: AbortSignal; memoryEnabled?: boolean }): Promise<HostToolResult> {
    switch (name) {
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
        const r = await this.gateway.generateImage(prompt.trim(), ctx.signal);
        if (!r.ok) return { ok: false, text: r.error, imageDataUri: null };
        return { ok: true, text: `Generated an image${r.revised ? ` (revised prompt: ${r.revised})` : ""}. The image follows in the next message.`, imageDataUri: r.dataUri };
      }
      case "run_python": {
        const code = args["code"];
        if (typeof code !== "string" || !code.trim()) return { ok: false, text: "'code' is required.", imageDataUri: null };
        const r = await this.chatEdge.runCode(code, [], ctx.signal);
        if (!r) return { ok: false, text: "Code interpreter call failed (chat-edge unreachable or refused).", imageDataUri: null };
        const lines: string[] = [`Exit code: ${r.exit}`];
        const stdout = (r.stdout ?? "").replace(/\n+$/, "");
        const stderr = (r.stderr ?? "").replace(/\n+$/, "");
        if (stdout) lines.push("", "stdout:", stdout);
        if (stderr && (r.exit !== 0 || !stdout)) lines.push("", "stderr:", stderr);
        if (r.truncated) lines.push("[output truncated]");
        if (!stdout && !stderr) lines.push("(no output was printed)");
        const images = r.images ?? [];
        if (images.length) lines.push("", "Generated image(s): " + images.map((i) => i.filename).join(", "));
        return { ok: true, text: lines.join("\n"), imageDataUri: images[0]?.data_uri ?? null };
      }
      case "search_past_conversations": {
        const query = args["query"];
        if (typeof query !== "string" || !query.trim()) return { ok: false, text: "'query' is required.", imageDataUri: null };
        const scopeRaw = typeof args["scope"] === "string" ? String(args["scope"]) : "all";
        const scope = ["all", "messages", "conversations", "memories"].includes(scopeRaw) ? scopeRaw : "all";
        const maxRaw = typeof args["max_results"] === "number" ? Math.floor(args["max_results"] as number) : 10;
        const limit = Math.max(1, Math.min(20, Number.isFinite(maxRaw) && maxRaw > 0 ? maxRaw : 10));
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
              const r = await this.runNested({ task: String(t.task ?? ""), tools: null, label, parentToolCallId: ctx.toolCallId, conversationId: ctx.conversationId, memoryEnabled: ctx.memoryEnabled }, ctx.signal);
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
        const r = await this.runNested({ task, tools: allow, parentToolCallId: ctx.toolCallId, conversationId: ctx.conversationId, memoryEnabled: ctx.memoryEnabled }, ctx.signal);
        if (r.completed) return { ok: true, text: r.answer + trace(r.trace), imageDataUri: null };
        if (r.stopReason === "failed") return { ok: false, text: `Sub-agent failed: ${r.answer}${trace(r.trace)}`, imageDataUri: null };
        return { ok: false, text: `Sub-agent did not finish: ${r.stopReason ?? "the run ended unexpectedly."}${trace(r.trace)}`, imageDataUri: null };
      }
      default:
        return { ok: false, text: `Unknown host tool '${name}'.`, imageDataUri: null };
    }
  }
}

function trace(entries: string[]): string {
  if (entries.length === 0) return "";
  const shown = entries.slice(0, MAX_TRACE_ENTRIES).join(", ");
  return `\n\n[Sub-agent tool calls: ${shown}${entries.length > MAX_TRACE_ENTRIES ? `, … ${entries.length - MAX_TRACE_ENTRIES} more` : ""}]`;
}
