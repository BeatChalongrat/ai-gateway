using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Events;

/// <summary>Events from the agent orchestrator during a tool-calling turn.</summary>
public abstract record AgentStreamEvent;

public sealed record AgentContentDeltaEvent(string Text) : AgentStreamEvent;

public sealed record AgentToolCallsEvent(IReadOnlyList<GatewayToolCall> ToolCalls) : AgentStreamEvent;

/// <summary>Raised when a tool needs user consent before execution.</summary>
public sealed record AgentToolConsentRequestEvent(
    string ToolCallId,
    string ToolName,
    string ArgumentsSummary,
    PermissionLevel CurrentPermission) : AgentStreamEvent;

/// <summary>A tool finished executing (or was denied).</summary>
public sealed record AgentToolResultEvent(
    string ToolCallId,
    string ToolName,
    string ArgumentsJson,
    bool Success,
    string ResultPreview,
    long DurationMs,
    // R9 E: full output for the expandable tool card + screenshot preview.
    string? FullResult = null,
    string? ImageDataUri = null) : AgentStreamEvent;

/// <summary>Stream finished (final answer, no more tool calls).</summary>
public sealed record AgentStreamDoneEvent : AgentStreamEvent;

/// <summary>Error from the gateway or during tool execution. <paramref name="Code"/>
/// is the gateway's machine-readable error code when the envelope carried one
/// (e.g. "context_length_exceeded").</summary>
public sealed record AgentErrorEvent(string Message, string? Code = null) : AgentStreamEvent;

/// <summary>Agent loop status update.</summary>
public sealed record AgentStatusEvent(string Status, int Iteration, int MaxIterations) : AgentStreamEvent;

/// <summary>Older messages were trimmed/pruned to fit the model's context window.</summary>
public sealed record AgentContextTrimmedEvent(int OmittedMessages) : AgentStreamEvent;

/// <summary>The gateway's router answered with a different model than requested
/// (agent-path twin of the chat-edge FallbackInfoEvent).</summary>
public sealed record AgentFallbackEvent(
    string RequestedModel,
    string ActualModel,
    string? Reason) : AgentStreamEvent;
