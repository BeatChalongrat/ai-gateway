using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>
/// Result of a server-side run_python execution (Phase 13 Part B). chat-edge
/// runs the code in its hardened sandbox and returns stdout/stderr plus any
/// artifacts the code wrote to /job/out — images inline as base64 data URIs,
/// other files summarised.
/// </summary>
public sealed record CodeExecutionResult(
    [property: JsonPropertyName("exit")] int Exit,
    [property: JsonPropertyName("stdout")] string? Stdout,
    [property: JsonPropertyName("stderr")] string? Stderr,
    [property: JsonPropertyName("timed_out")] bool TimedOut,
    [property: JsonPropertyName("truncated")] bool Truncated,
    [property: JsonPropertyName("images")] List<CodeExecutionImage>? Images,
    [property: JsonPropertyName("files")] List<CodeExecutionFile>? Files);

public sealed record CodeExecutionImage(
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("data_uri")] string DataUri);

public sealed record CodeExecutionFile(
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("content_type")] string ContentType,
    [property: JsonPropertyName("size_bytes")] int SizeBytes);

// ── Canvas artifacts (Phase 15 / server C3, migration 030) ──────────────────
//
// One row per VERSION. (conversation_id, artifact_key) is the lineage and the
// highest version is current. The desktop and the web write the same lineage,
// which is why artifact_key must be derived identically on both
// (CanvasEligibility.ArtifactKeyFor).

public sealed record CanvasArtifactDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("conversation_id")] string ConversationId,
    [property: JsonPropertyName("artifact_key")] string ArtifactKey,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("version")] int Version,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("message_id")] string? MessageId = null,
    [property: JsonPropertyName("title")] string? Title = null);

public sealed record CanvasArtifactListResponse(
    [property: JsonPropertyName("artifacts")] List<CanvasArtifactDto> Artifacts);

public sealed record CanvasVersionListResponse(
    [property: JsonPropertyName("versions")] List<CanvasArtifactDto> Versions);

public sealed record CanvasArtifactEnvelope(
    [property: JsonPropertyName("artifact")] CanvasArtifactDto Artifact);

public sealed record SaveCanvasArtifactRequest(
    [property: JsonPropertyName("artifact_key")] string ArtifactKey,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("message_id")] string MessageId);

// ── Media generation (Phase 14 Task 3) ──────────────────────────────────────
//
// Audio generation is DIRECT DISPATCH, not a model-callable tool. There is no
// generate_audio in chat-edge's tool registry (unlike generate_image /
// generate_video): the only path is POST /audio/generations, which chat-edge
// proxies to the gateway, returning a job the client polls. That is why the
// desktop gets an audio FORM rather than the composer toggle image and video
// use — a toggle would bias the model toward a tool that does not exist.

/// <summary>POST /api/v1/audio/generations. mode="tts" speaks text with a
/// voice; mode="music" generates a track from a prompt.</summary>
public sealed record AudioGenerationRequest(
    [property: JsonPropertyName("prompt")] string Prompt,
    [property: JsonPropertyName("model")] string Model,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("voice_id")] string? VoiceId = null,
    [property: JsonPropertyName("duration_sec")] int? DurationSec = null,
    [property: JsonPropertyName("conversation_id")] string? ConversationId = null);

/// <summary>The submit response: a job id plus the cost the gateway reserved.</summary>
public sealed record MediaGenSubmitResponse(
    [property: JsonPropertyName("job_id")] string JobId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("estimated_cost_usd")] string? EstimatedCostUsd = null,
    [property: JsonPropertyName("balance_after_usd")] string? BalanceAfterUsd = null,
    // Set by the server when the estimate crosses its confirmation threshold;
    // web acts on it (CostConfirmModal). Desktop deliberately does NOT — by
    // product decision, users hold their own credit balance, so paid media is
    // not gated per job here. L1 (session budget) and L2 (tenant daily cap) in
    // backend spend_guard.py still apply. Intentionally read-but-unused on
    // desktop — do not "fix" it.
    [property: JsonPropertyName("requires_confirmation")] bool RequiresConfirmation = false);

/// <summary>GET /api/v1/jobs/{id} — poll until status is a terminal value.</summary>
public sealed record MediaJobStatus(
    [property: JsonPropertyName("job_id")] string JobId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("progress_pct")] int? ProgressPct = null,
    [property: JsonPropertyName("output_url")] string? OutputUrl = null,
    [property: JsonPropertyName("error_code")] string? ErrorCode = null,
    [property: JsonPropertyName("error_message")] string? ErrorMessage = null);
