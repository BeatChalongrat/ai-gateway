using System.Text.Json;
using System.Text.Json.Serialization;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Models;

/// <summary>POST body for the gateway's OpenAI-compatible /v1/chat/completions.</summary>
public sealed class GatewayCompletionRequest
{
    [JsonPropertyName("model")]
    public required string Model { get; init; }

    [JsonPropertyName("messages")]
    public required List<GatewayMessage> Messages { get; init; }

    [JsonPropertyName("stream")]
    public bool Stream { get; init; } = true;

    /// <summary>Output-token cap; the gateway accepts 1–128000.</summary>
    [JsonPropertyName("max_tokens")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? MaxTokens { get; init; }

    [JsonPropertyName("tools")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<GatewayToolDefinition>? Tools { get; init; }

    [JsonPropertyName("tool_choice")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? ToolChoice { get; init; }
}

public sealed class GatewayMessage
{
    [JsonPropertyName("role")]
    public required string Role { get; init; }

    /// <summary>Plain string, or an array of OpenAI content blocks (multimodal:
    /// {type:"text"} / {type:"image_url"}). Serialized verbatim either way.</summary>
    [JsonPropertyName("content")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public object? Content { get; init; }

    /// <summary>Only on assistant messages that invoked tools.</summary>
    [JsonPropertyName("tool_calls")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<GatewayToolCall>? ToolCalls { get; init; }

    /// <summary>Only on role:"tool" messages.</summary>
    [JsonPropertyName("tool_call_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? ToolCallId { get; init; }

    /// <summary>Only on role:"tool" messages.</summary>
    [JsonPropertyName("name")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Name { get; init; }
}

public sealed class GatewayToolDefinition
{
    [JsonPropertyName("type")]
    public string Type { get; init; } = "function";

    [JsonPropertyName("function")]
    public required GatewayFunctionDef Function { get; init; }
}

public sealed class GatewayFunctionDef
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("description")]
    public required string Description { get; init; }

    [JsonPropertyName("parameters")]
    public required JsonElement Parameters { get; init; }
}

public sealed class GatewayToolCall
{
    [JsonPropertyName("id")]
    public required string Id { get; init; }

    [JsonPropertyName("type")]
    public string Type { get; init; } = "function";

    [JsonPropertyName("function")]
    public required GatewayToolCallFunction Function { get; init; }
}

public sealed class GatewayToolCallFunction
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("arguments")]
    public required string Arguments { get; init; }
}

public sealed record ComputerActionResult(
    bool ActionRequested,
    bool TargetResolved,
    bool TargetValidated,
    bool ExecutionAttempted,
    bool OsInputAccepted,
    bool EffectVerified,
    bool TaskCompleted,
    string? FailureCode,
    string? FailureReason,
    bool Retryable,
    IReadOnlyList<string> Evidence,
    string? CaptureBeforeId,
    string? CaptureAfterId,
    long? WindowHandle,
    int? ProcessId,
    int? ExecutedX,
    int? ExecutedY,
    int? TargetX,
    int? TargetY,
    int? TargetWidth,
    int? TargetHeight,
    long DurationMs,
    bool TargetSelectedCorrectly = false,
    bool TargetActivated = false);

public sealed record ToolEffectResult(
    bool NativeSuccess,
    bool ExpectedEffectObserved,
    bool Verified,
    string? FailureCode,
    string? FailureReason,
    bool Retryable,
    IReadOnlyList<string> Evidence,
    int? ExitCode = null,
    int? StatusCode = null,
    IReadOnlyList<string>? SideEffects = null);

/// <summary>
/// Result of one local tool execution. Success means the tool protocol
/// completed; ComputerAction independently records whether the UI effect and
/// user task were actually verified.
/// </summary>
public sealed record ToolResult(
    bool Success,
    string Content,
    string? Error = null,
    string? ImageDataUri = null,
    ComputerActionResult? ComputerAction = null,
    ToolEffectResult? Effect = null)
{
    private const int MaxContentChars = 50_000;

    public static ToolResult Ok(string content) => new(true, content);
    public static ToolResult Fail(string error) => new(false, string.Empty, error);

    /// <summary>
    /// Success result carrying an image (e.g. a screenshot) for vision analysis.
    ///
    /// <paramref name="evidence"/> is what the capture actually observed. It
    /// matters because the runtime no longer infers "the intended effect was
    /// observed" from a read-only tool merely returning success — a tool that
    /// really did look at something has to say what it saw, or it cannot verify
    /// an outcome.
    /// </summary>
    public static ToolResult OkWithImage(
        string content,
        string imageDataUri,
        IReadOnlyList<string>? evidence = null) =>
        new(true, content, null, imageDataUri, null,
            evidence is null
                ? null
                : new ToolEffectResult(true, true, true, null, null, false, evidence));

    public static ToolResult FromComputerAction(
        string content, ComputerActionResult action) =>
        new(true, content, null, null, action);

    public static ToolResult FailedComputerAction(
        string error, ComputerActionResult action) =>
        new(false, string.Empty, error, null, action);

    public static ToolResult Verified(
        string content,
        IReadOnlyList<string> evidence,
        IReadOnlyList<string>? sideEffects = null,
        int? exitCode = null) =>
        new(true, content, null, null, null,
            new ToolEffectResult(true, true, true, null, null, false,
                evidence, exitCode, null, sideEffects));

    public static ToolResult ExecutedUnverified(
        string content,
        string failureCode,
        string failureReason,
        bool retryable = true,
        IReadOnlyList<string>? evidence = null,
        IReadOnlyList<string>? sideEffects = null) =>
        new(true, content, null, null, null,
            new ToolEffectResult(true, false, false, failureCode, failureReason,
                retryable, evidence ?? Array.Empty<string>(), null, null, sideEffects));

    public static ToolResult FailedEffect(
        string failureCode,
        string failureReason,
        bool retryable,
        int? exitCode = null,
        string? content = null) =>
        new(false, content ?? string.Empty, failureReason, null, null,
            new ToolEffectResult(false, false, false, failureCode, failureReason,
                retryable, Array.Empty<string>(), exitCode));

    /// <summary>Truncated so a huge file read / command output can't blow the LLM context.</summary>
    public string TruncatedContent => Content.Length > MaxContentChars
        ? Content[..MaxContentChars] + "\n\n[...truncated to 50K chars]"
        : Content;
}

public sealed record ToolDataFlowPolicy(
    string LocalAction,
    string? ExternalAction,
    string SourceType,
    string Sensitivity,
    string? Destination,
    bool ExternalTransmission,
    bool AlwaysRequireConfirmation,
    bool AllowPersistence = false,
    int? MaxPayloadBytes = null);

public sealed record ToolCapabilityDescriptor(
    string Name,
    ToolCategory Category,
    bool ReadOnly,
    bool Reversible,
    bool Idempotent,
    bool ExternalSideEffect,
    bool LocalSideEffect,
    bool RequiresNetwork,
    bool RequiresWorkspaceWrite,
    bool RequiresUserConfirmation);

public sealed record ToolRequestValidation(bool Valid, string? FailureCode, string? Reason)
{
    public static ToolRequestValidation Accepted { get; } = new(true, null, null);
}

public sealed record PreparedToolRequest(
    string ArgumentsFingerprint,
    DateTimeOffset PreparedAtUtc)
{
    public InteractionActionDescriptor? Action { get; init; }
    public InteractionObservationSnapshot? PreActionObservation { get; init; }
}

public sealed record StructuredToolObservation(
    string SourceType,
    ObservationTrust TrustLevel,
    SensitiveDataClassification Sensitivity,
    string ContentFingerprint,
    int ByteCount,
    IReadOnlyList<string> AllowedUses,
    IReadOnlyList<string> ProhibitedUses)
{
    public InteractionObservationSnapshot? PostActionObservation { get; init; }
}

public sealed record ToolContractVerification(
    VerificationDisposition Disposition,
    IReadOnlyList<string> Evidence,
    string Reason);

public sealed record ToolReconciliationResult(
    VerificationDisposition Disposition,
    bool SafeToRetry,
    string Reason);

public sealed record ToolCompensationResult(bool Supported, bool Succeeded, string Reason);

public sealed record ToolConsentRequest(
    string ToolCallId,
    string ToolName,
    string Description,
    Domain.Enums.ToolCategory Category,
    string ArgumentsJson,
    string Scope,
    string? Action = null,
    string? Destination = null,
    string? Sensitivity = null,
    bool ExternalTransmission = false,
    bool AlwaysRequireConfirmation = false);

public sealed record ToolConsentResponse(bool Denied, bool AllowOnce, bool AllowAlways)
{
    public static ToolConsentResponse Deny { get; } = new(true, false, false);
    public static ToolConsentResponse Once { get; } = new(false, true, false);
    public static ToolConsentResponse Always { get; } = new(false, false, true);
}
