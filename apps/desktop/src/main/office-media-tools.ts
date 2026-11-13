import type { ChatEdgeClient } from "./chat-edge-client";
import type { HostToolContext, HostToolDefinition, HostToolResult } from "./host-tools";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const str = { type: "string" };
export const OFFICE_MEDIA_TOOLS: Record<string, HostToolDefinition> = {
  edit_office_file: {
    name: "edit_office_file",
    description: "Edit files in this conversation. visual_inspect returns DOCX paragraphs, PPTX text frames, native PDF text objects or image controls. visual_edit applies plain text/style/box edits with inspected sha256 and page; native PDF editing excludes scans/forms/rotation. ai_repair inspects rendered Word/PPT/images through the configured vision model, preserves Office text, and creates a proposal requiring user review before saving (may use model credits). Inspect first. Use inspect/edit for XLSX, CSV and text. history/restore preserve versions. Local files must be imported first.",
    inputSchema: { type: "object", properties: { file_id: str, mode: { type: "string", enum: ["inspect", "edit", "history", "restore", "visual_inspect", "visual_edit", "ai_repair"] }, sha256: str, page: { type: "integer", minimum: 0 }, image: { type: "object", properties: { rotation: { type: "integer", enum: [0, 90, 180, 270] }, brightness: { type: "number", minimum: .25, maximum: 2 }, contrast: { type: "number", minimum: .25, maximum: 2 } } }, sheet: str, offset: { type: "integer" }, target_id: str, changes: { type: "array", items: { type: "object", properties: { id: str, value: str, style: { type: "object", properties: { size: { type: "number", minimum: 6, maximum: 144 }, bold: { type: "boolean" }, italic: { type: "boolean" }, underline: { type: "boolean" }, color: str, align: { type: "string", enum: ["left", "center", "right", "justify"] }, space_after: { type: "number", minimum: 0, maximum: 144 } }, additionalProperties: false }, box: { type: "object", properties: { left: { type: "number" }, top: { type: "number" }, width: { type: "number" }, height: { type: "number" } }, required: ["left", "top", "width", "height"], additionalProperties: false }, type: { type: "string", enum: ["text", "number", "formula"] } }, required: ["id", "value"] } } }, required: ["file_id", "mode"] },
  },
  generate_video: {
    name: "generate_video",
    description: "Submit a generated video using the configured provider. Preserve user constraints in the prompt. Returns a durable job ID, not a finished video. The chat video jobs panel handles playback, download and explicit cost confirmation. Never confirm spending yourself. For deterministic motion graphics use run_python with motion_templates instead.",
    inputSchema: { type: "object", properties: { prompt: str, duration_sec: { type: "integer", minimum: 1, maximum: 60 } }, required: ["prompt"] },
  },
  video_job: {
    name: "video_job", description: "Read status or cancel one video job in the current conversation. No confirmation action is available to agents. Done output is playable/downloadable from the video jobs panel.",
    inputSchema: { type: "object", properties: { job_id: str, action: { type: "string", enum: ["status", "cancel"] } }, required: ["job_id", "action"] },
  },
};

export async function officeMediaTool(name: string, args: Record<string, unknown>, ctx: HostToolContext, client: ChatEdgeClient): Promise<HostToolResult> {
  const answer = (value: unknown): HostToolResult => ({ ok: true, text: JSON.stringify(value), imageDataUri: null });
  try {
    if (!uuid.test(ctx.conversationId)) throw new Error("A saved conversation is required.");
    if (name === "edit_office_file") {
      if (typeof args.file_id !== "string" || !uuid.test(args.file_id)) throw new Error("Invalid file ID.");
      const { file_id, ...request } = args;
      const result = await client.officeMediaRequest<{ visual?: Record<string, unknown>; file?: NonNullable<HostToolResult["attachedFiles"]>[number] }>(`/files/${file_id}/editor`, { ...request, mode: args.mode === "ai_repair" ? "ai_propose" : args.mode, conversation_id: ctx.conversationId }, ctx.signal, args.mode === "ai_repair" ? 660_000 : 240_000);
      if (result.visual) { delete result.visual.background; delete result.visual.pages; delete result.visual.blocks; }
      return { ...answer(result), ...(result.file ? { attachedFiles: [result.file] } : {}) };
    }
    if (name === "generate_video") {
      if (typeof args.prompt !== "string" || !args.prompt.trim() || args.prompt.length > 12000) throw new Error("Supply a video prompt of at most 12000 characters.");
      const config = await client.officeMediaRequest<{ enabled: boolean; reason?: string; agent_model?: string; supported_controls?: { duration_sec: number[] } }>("/video/config", undefined, ctx.signal);
      if (!config.enabled || !config.agent_model) throw new Error(config.reason || "No agent video model configured.");
      const duration = args.duration_sec ?? 5;
      if (typeof duration !== "number" || !Number.isInteger(duration) || duration < 1 || duration > Math.min(config.supported_controls?.duration_sec?.[1] ?? 10, 60)) throw new Error("Duration exceeds the configured provider limit.");
      return answer(await client.officeMediaRequest("/videos/generations", { model: config.agent_model, prompt: args.prompt.trim(), duration_sec: duration, resolution: "auto", has_audio: false, conversation_id: ctx.conversationId }, ctx.signal));
    }
    if (name !== "video_job" || typeof args.job_id !== "string" || !uuid.test(args.job_id) || !["status", "cancel"].includes(String(args.action))) throw new Error("Invalid video job action.");
    const list = await client.officeMediaRequest<{ jobs: { job_id: string }[] }>(`/jobs?conversation_id=${ctx.conversationId}`, undefined, ctx.signal);
    if (!list.jobs.some(job => job.job_id === args.job_id)) throw new Error("Video job is not in this conversation's recent jobs.");
    return answer(await client.officeMediaRequest(`/jobs/${args.job_id}${args.action === "cancel" ? "/cancel" : ""}`, args.action === "cancel" ? {} : undefined, ctx.signal));
  } catch (e) { return { ok: false, text: e instanceof Error ? e.message : String(e), imageDataUri: null }; }
}
