using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>Lifecycle of one long-lived worker session. Transitions are validated
/// centrally so a session can never appear usable after it has failed.</summary>
public enum InteractiveSessionState
{
    Created,
    Starting,
    Handshaking,
    Ready,
    Busy,
    Draining,
    Stopped,

    // Failure states — all terminal.
    StartFailed,
    ProtocolFailed,
    Crashed,
    TimedOut,
    UnknownOutcome,
    Cancelled,
    ForceTerminated
}

/// <summary>
/// How far a request got before something went wrong. This is the distinction the
/// whole transport exists to preserve: a request still sitting in our queue
/// provably did nothing, while one already written to the worker's stdin may
/// already have taken effect even though the answer never came back.
/// </summary>
public enum DispatchStage
{
    /// <summary>Accepted locally, nothing written to the worker yet.</summary>
    Queued,

    /// <summary>Bytes are being written; the worker may have parsed a prefix.</summary>
    Writing,

    /// <summary>The full frame reached the worker's pipe.</summary>
    Dispatched,

    /// <summary>The worker explicitly acknowledged the request id.</summary>
    Acknowledged,

    /// <summary>A response was correlated back.</summary>
    Completed
}

public enum InteractiveRequestOutcome
{
    /// <summary>Never left our queue — safe to retry.</summary>
    FailedNoEffect,

    /// <summary>Reached the worker; effect unknown. Never auto-retried.</summary>
    UnknownOutcome,

    Succeeded,
    FailedByWorker,
    TimedOut,
    CancelledBeforeDispatch,
    CancelledAfterDispatchUnknown,
    CancelledConfirmed
}

/// <summary>Identity of one live worker session. The generation makes responses
/// from a previous process instance impossible to confuse with this one's.</summary>
public sealed record InteractiveWorkerSessionInfo(
    string SessionId,
    string WorkerId,
    int ProcessId,
    string? RunId,
    string? StepId,
    string WorkerVersion,
    string ExecutableIdentity,
    string SandboxIdentity,
    long Generation,
    DateTimeOffset CreatedUtc,
    DateTimeOffset LastHeartbeatUtc,
    InteractiveSessionState State);

/// <summary>
/// Envelope for every message on the wire. Correlation fields travel with the
/// message so a response can be tied back to the AgentRun that asked for it —
/// and so a frame from an earlier attempt is rejected rather than believed.
/// </summary>
public sealed record InteractiveFrame
{
    [JsonPropertyName("protocolVersion")]
    public int ProtocolVersion { get; init; } = 1;

    /// <summary>request | response | notification | error | heartbeat |
    /// shutdown | cancel | ack</summary>
    [JsonPropertyName("messageType")]
    public required string MessageType { get; init; }

    [JsonPropertyName("sessionId")]
    public string? SessionId { get; init; }

    [JsonPropertyName("requestId")]
    public string? RequestId { get; init; }

    [JsonPropertyName("runId")]
    public string? RunId { get; init; }

    [JsonPropertyName("stepId")]
    public string? StepId { get; init; }

    [JsonPropertyName("attemptId")]
    public string? AttemptId { get; init; }

    [JsonPropertyName("generation")]
    public long Generation { get; init; }

    [JsonPropertyName("method")]
    public string? Method { get; init; }

    /// <summary>Opaque body. The transport never interprets it — that belongs to
    /// the protocol layer above (MCP), which keeps this framing reusable.</summary>
    [JsonPropertyName("payload")]
    public string? Payload { get; init; }

    [JsonPropertyName("errorCode")]
    public string? ErrorCode { get; init; }

    [JsonPropertyName("errorMessage")]
    public string? ErrorMessage { get; init; }
}

public sealed record InteractiveRequestResult(
    InteractiveRequestOutcome Outcome,
    DispatchStage Stage,
    string? Payload,
    string? ErrorCode,
    string? ErrorMessage,
    long DurationMs)
{
    public bool Succeeded => Outcome == InteractiveRequestOutcome.Succeeded;

    /// <summary>True when the real state must be inspected before anything is
    /// repeated.</summary>
    public bool RequiresReconciliation =>
        Outcome is InteractiveRequestOutcome.UnknownOutcome
            or InteractiveRequestOutcome.CancelledAfterDispatchUnknown;
}

/// <summary>Durable event names the transport emits. Kept as constants so the
/// runtime and tests agree on the vocabulary.</summary>
public static class InteractiveWorkerEvents
{
    public const string SessionStarting = "WorkerSessionStarting";
    public const string SessionReady = "WorkerSessionReady";
    public const string RequestQueued = "RequestQueued";
    public const string DispatchStarted = "DispatchStarted";
    public const string DispatchAcknowledged = "DispatchAcknowledged";
    public const string NotificationReceived = "NotificationReceived";
    public const string ResponseReceived = "ResponseReceived";
    public const string RequestTimedOut = "RequestTimedOut";
    public const string WorkerDisconnected = "WorkerDisconnected";
    public const string UnknownOutcomeDetected = "UnknownOutcomeDetected";
    public const string CancellationRequested = "CancellationRequested";
    public const string SessionStopped = "SessionStopped";
    public const string CleanupCompleted = "CleanupCompleted";
}

public static class InteractiveSessionStateMachine
{
    public static bool CanTransition(InteractiveSessionState from, InteractiveSessionState to)
    {
        if (from == to)
            return true;
        if (IsTerminal(from))
            return false;
        // Any live state may fail or be cancelled.
        if (IsTerminal(to) && to != InteractiveSessionState.Stopped)
            return true;

        return from switch
        {
            InteractiveSessionState.Created => to is InteractiveSessionState.Starting,
            InteractiveSessionState.Starting => to is InteractiveSessionState.Handshaking,
            InteractiveSessionState.Handshaking => to is InteractiveSessionState.Ready,
            InteractiveSessionState.Ready =>
                to is InteractiveSessionState.Busy or InteractiveSessionState.Draining
                    or InteractiveSessionState.Stopped,
            InteractiveSessionState.Busy =>
                to is InteractiveSessionState.Ready or InteractiveSessionState.Draining,
            InteractiveSessionState.Draining => to is InteractiveSessionState.Stopped,
            _ => false
        };
    }

    public static bool IsTerminal(InteractiveSessionState state) =>
        state is InteractiveSessionState.Stopped
            or InteractiveSessionState.StartFailed
            or InteractiveSessionState.ProtocolFailed
            or InteractiveSessionState.Crashed
            or InteractiveSessionState.TimedOut
            or InteractiveSessionState.UnknownOutcome
            or InteractiveSessionState.Cancelled
            or InteractiveSessionState.ForceTerminated;

    public static bool CanAcceptRequests(InteractiveSessionState state) =>
        state is InteractiveSessionState.Ready or InteractiveSessionState.Busy;
}
