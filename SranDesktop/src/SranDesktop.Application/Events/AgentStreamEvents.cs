using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Events;

/// <summary>Events from the agent orchestrator during a tool-calling turn.</summary>
public abstract record AgentStreamEvent;

public sealed record AgentContentDeltaEvent(string Text) : AgentStreamEvent;

public sealed record AgentToolCallsEvent(IReadOnlyList<GatewayToolCall> ToolCalls) : AgentStreamEvent;

/// <summary>The only plan projection rendered by the chat UI.</summary>
public sealed record AgentAuthoritativePlanEvent(AuthoritativePlanView Plan) : AgentStreamEvent;

/// <summary>
/// Monotonic persisted runtime events. The UI consumes event type and sequence,
/// never model-authored status text, to show durable progress.
/// </summary>
public sealed record AgentDurableEventsEvent(
    string RunId,
    IReadOnlyList<TaskEvent> Events) : AgentStreamEvent;

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
    string? ImageDataUri = null,
    ComputerActionResult? ComputerAction = null,
    AgentToolResult? UnifiedResult = null) : AgentStreamEvent;

/// <summary>
/// R4 (round 2a): the runtime is about to auto-retry a transient computer-use failure
/// WITHOUT the model — it takes a fresh observation and re-dispatches the SAME logical
/// action, collapsed into one card. <paramref name="Attempt"/> is the 1-based attempt
/// about to run, <paramref name="MaxAttempts"/> the retry budget, <paramref name="Reason"/>
/// the retryable FailureCode that triggered it. Additive/backward-compatible: existing
/// consumers switch on event type and ignore unknown kinds (the round-2b UI consumes it).
/// </summary>
public sealed record AgentToolRetryEvent(
    string ToolCallId,
    string ToolName,
    int Attempt,
    int MaxAttempts,
    string Reason) : AgentStreamEvent;

/// <summary>Stream finished (final answer, no more tool calls).</summary>
public sealed record AgentStreamDoneEvent : AgentStreamEvent;

/// <summary>Error from the gateway or during tool execution. <paramref name="Code"/>
/// is the gateway's machine-readable error code when the envelope carried one
/// (e.g. "context_length_exceeded").</summary>
public sealed record AgentErrorEvent(string Message, string? Code = null) : AgentStreamEvent;

/// <summary>Agent loop status update.</summary>
public sealed record AgentStatusEvent(string Status, int Iteration, int MaxIterations) : AgentStreamEvent;

/// <summary>
/// The turn hit its iteration/time budget with work still in flight (Phase A
/// PART 1/6). Unlike <see cref="AgentErrorEvent"/> this is a CHECKPOINT, not a
/// failure: the accumulated tool history is intact in the caller's message list
/// and the loop can be re-entered to continue the task. The UI surfaces a
/// "Continue" affordance instead of a terminal error.
/// </summary>
public sealed record AgentPausedEvent(string Reason) : AgentStreamEvent;

/// <summary>Older messages were trimmed/pruned to fit the model's context window.</summary>
public sealed record AgentContextTrimmedEvent(int OmittedMessages) : AgentStreamEvent;

/// <summary>
/// Raised once when the durable runtime is available but declined to open a
/// durable task for this turn (the request wasn't interpreted as an actionable
/// goal), so the outcome-verification guarantees are NOT in effect and the turn
/// runs as a plain conversation. Purely informational transparency — the user
/// isn't asked to do anything.
/// </summary>
public sealed record AgentVerificationModeEvent(string Message) : AgentStreamEvent;

/// <summary>The gateway's router answered with a different model than requested
/// (agent-path twin of the chat-edge FallbackInfoEvent).</summary>
public sealed record AgentFallbackEvent(
    string RequestedModel,
    string ActualModel,
    string? Reason) : AgentStreamEvent;
