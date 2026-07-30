using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>
/// How a worker attempt ended, from the HOST's point of view. This is the only
/// vocabulary the durable runtime consumes — deliberately not "exit code", because
/// a zero exit proves the process ended, not that the work happened or is correct.
/// </summary>
public enum WorkerOutcomeClass
{
    /// <summary>Rejected before anything ran: bad request, untrusted capability,
    /// worker image unavailable. Guaranteed no side effect.</summary>
    PreDispatchFailure,

    /// <summary>The worker was started and may have acted, but its answer was lost
    /// (crash, timeout, disconnect). The real state must be inspected before any
    /// retry — this must never be downgraded to FailedNoEffect.</summary>
    PostDispatchUnknownOutcome,

    /// <summary>The worker ran and reported a clean failure with nothing changed.</summary>
    FailedNoEffect,

    /// <summary>The worker changed something and then failed. Requires reconciliation
    /// or rollback.</summary>
    FailedPartialEffect,

    /// <summary>Failed for a reason a retry cannot fix.</summary>
    FailedPermanent,

    /// <summary>The worker reported success but the host could not confirm it.
    /// Never presented to the user as a completed action.</summary>
    SucceededUnverified,

    /// <summary>The worker reported success AND the host independently confirmed it.</summary>
    SucceededVerified,

    /// <summary>Only a human can resolve the state.</summary>
    NeedsUser
}

/// <summary>What the worker is allowed to reach. Anything not listed is denied.</summary>
public sealed record WorkerNetworkPolicy(bool Allowed, IReadOnlyList<string> AllowedHosts)
{
    public static WorkerNetworkPolicy Denied { get; } = new(false, []);
}

public sealed record WorkerResourceQuotas(
    TimeSpan Deadline,
    long MaxOutputBytes,
    long MaxWorkspaceBytes,
    int MaxWorkspaceFiles)
{
    public static WorkerResourceQuotas Default { get; } = new(
        TimeSpan.FromMinutes(2),
        2 * 1024 * 1024,
        100L * 1024 * 1024,
        10_000);
}

/// <summary>
/// One unit of isolated work. Serialized into the worker's private working
/// directory; the worker never receives host environment, credentials, or paths
/// outside <see cref="WorkingDirectory"/>.
/// </summary>
public sealed record IsolatedWorkerRequest
{
    public required string RunId { get; init; }
    public required string StepId { get; init; }
    public required string AttemptId { get; init; }
    public required string IdempotencyKey { get; init; }

    /// <summary>Worker image identity (e.g. "document"), not a filesystem path.
    /// A model-supplied path must never select an executable.</summary>
    public required string Capability { get; init; }

    /// <summary>Typed operation name the worker dispatches on.</summary>
    public required string Operation { get; init; }

    /// <summary>Operation payload; validated against the worker's schema.</summary>
    public required string PayloadJson { get; init; }

    /// <summary>Directory the worker is given; the only path it can read or write.</summary>
    public required string WorkingDirectory { get; init; }

    public WorkerNetworkPolicy Network { get; init; } = WorkerNetworkPolicy.Denied;
    public WorkerResourceQuotas Quotas { get; init; } = WorkerResourceQuotas.Default;
    public string Sensitivity { get; init; } = "internal";
    public string TrustContext { get; init; } = "untrusted-output";

    /// <summary>What the host will check to decide verified vs unverified.</summary>
    public string? ExpectedOutputFile { get; init; }
    public string VerificationPolicy { get; init; } = "reopen-and-validate";
}

/// <summary>Structured note produced inside the worker. Untrusted by default —
/// it is derived from file contents the agent did not author.</summary>
public sealed record WorkerObservation(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("detail")] string Detail);

/// <summary>
/// The worker's own report, written to disk as response.json. Advisory only:
/// <see cref="Succeeded"/> is a claim, never the host's verdict.
/// </summary>
public sealed record IsolatedWorkerResponse
{
    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; init; } = 1;

    [JsonPropertyName("run_id")]
    public string RunId { get; init; } = "";

    [JsonPropertyName("step_id")]
    public string StepId { get; init; } = "";

    [JsonPropertyName("attempt_id")]
    public string AttemptId { get; init; } = "";

    [JsonPropertyName("idempotency_key")]
    public string IdempotencyKey { get; init; } = "";

    [JsonPropertyName("operation")]
    public string Operation { get; init; } = "";

    [JsonPropertyName("succeeded")]
    public bool Succeeded { get; init; }

    [JsonPropertyName("failure_code")]
    public string? FailureCode { get; init; }

    [JsonPropertyName("failure_reason")]
    public string? FailureReason { get; init; }

    /// <summary>True when the worker changed something outside its own scratch
    /// space; drives partial-effect classification on failure.</summary>
    [JsonPropertyName("side_effect_performed")]
    public bool SideEffectPerformed { get; init; }

    [JsonPropertyName("started_utc")]
    public DateTimeOffset StartedUtc { get; init; }

    [JsonPropertyName("completed_utc")]
    public DateTimeOffset CompletedUtc { get; init; }

    [JsonPropertyName("output_files")]
    public IReadOnlyList<string> OutputFiles { get; init; } = [];

    [JsonPropertyName("observations")]
    public IReadOnlyList<WorkerObservation> Observations { get; init; } = [];

    [JsonPropertyName("result_json")]
    public string? ResultJson { get; init; }

    [JsonPropertyName("temporary_artifacts")]
    public IReadOnlyList<string> TemporaryArtifacts { get; init; } = [];
}

/// <summary>The host's verdict after inspecting what the worker actually left behind.</summary>
public sealed record IsolatedWorkerOutcome(
    WorkerOutcomeClass Class,
    IsolatedWorkerResponse? Response,
    string? FailureCode,
    string? FailureReason,
    IReadOnlyList<string> Evidence,
    long DurationMs,
    bool TemporaryArtifactsRemoved)
{
    public bool Verified => Class == WorkerOutcomeClass.SucceededVerified;

    /// <summary>An unresolved outcome must be reconciled against real state before
    /// the runtime is allowed to repeat anything.</summary>
    public bool RequiresReconciliation =>
        Class is WorkerOutcomeClass.PostDispatchUnknownOutcome
            or WorkerOutcomeClass.FailedPartialEffect;
}
