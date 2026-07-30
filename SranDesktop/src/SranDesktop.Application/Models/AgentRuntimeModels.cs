using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Models;

public sealed class RuntimeStabilizationOptions
{
    public bool SemanticOutcomeEnforcement { get; init; } = true;
    public bool TargetBoundInput { get; init; } = true;
    public bool RuntimeOwnedCapabilityRouting { get; init; } = true;
    public bool ApplicationReadiness { get; init; } = true;
    public bool AuthoritativePlanUI { get; init; } = true;
    public bool VerifiedRecovery { get; init; } = true;
    public bool RealRuntimeMetrics { get; init; } = true;

    /// <summary>How long a granted tool-call approval stays valid before it must
    /// be re-approved. Single source for the runtime's approval expiry (was a
    /// hardcoded 5 minutes in ChatOrchestrator); default preserves that value.</summary>
    public TimeSpan ApprovalExpiry { get; init; } = TimeSpan.FromMinutes(5);
}

public sealed record AgentToolResult(
    string ToolCallId,
    string TaskId,
    string PlanId,
    string MilestoneId,
    string StepId,
    string ActionId,
    string ToolName,
    bool Requested,
    bool Authorized,
    bool ExecutionAttempted,
    bool Executed,
    bool NativeSuccess,
    int? ExitCode,
    int? StatusCode,
    bool OutputParsed,
    bool ExpectedEffectObserved,
    bool Verified,
    bool StepCompleted,
    string? FailureCode,
    string? FailureReason,
    bool Retryable,
    IReadOnlyList<string> SideEffects,
    IReadOnlyList<string> EvidenceIds,
    DateTimeOffset StartedAtUtc,
    DateTimeOffset CompletedAtUtc,
    long DurationMs,
    SensitiveDataClassification SensitiveOutputClassification)
{
    public string? GoalId { get; init; }
    public string? OutcomeId { get; init; }
    public int PlanVersion { get; init; }
    public bool Dispatched { get; init; }
    public bool NativeAccepted { get; init; }
    public bool TargetResolved { get; init; }
    public bool TargetValidated { get; init; }
    public bool ReceiverConfirmed { get; init; }
    public bool EffectObserved { get; init; }
    public bool EffectMatchedOutcome { get; init; }
    public bool UnknownOutcome { get; init; }
    public string? ActualApplication { get; init; }
    public long? ActualHwnd { get; init; }
    public int? ActualPid { get; init; }
    public string? ActualControlIdentity { get; init; }
    public string? BeforeState { get; init; }
    public string? AfterState { get; init; }
    public string? VerificationReason { get; init; }
}

public enum ApplicationReadinessLevel
{
    NotStarted,
    ProcessStarted,
    WindowLocated,
    WindowVisible,
    ForegroundConfirmed,
    AccessibilityReady,
    TargetControlReady,
    InputReady,
    EffectObservable,
    ReadyForRequestedAction
}

public sealed record ApplicationReadinessResult(
    ApplicationReadinessLevel Level,
    bool Ready,
    string Application,
    long? WindowHandle,
    int? ProcessId,
    string? ControlIdentity,
    bool WindowVisible,
    bool ForegroundConfirmed,
    bool AccessibilityReady,
    bool TargetControlReady,
    bool InputReceiverConfirmed,
    bool BlockingOverlayDetected,
    string? FailureCode,
    string Reason,
    IReadOnlyList<string> Evidence);

public sealed record AgentActionRequest
{
    public required string ActionId { get; init; }
    public required string TaskId { get; init; }
    public required string GoalId { get; init; }
    public required string PlanId { get; init; }
    public int PlanVersion { get; init; }
    public required string MilestoneId { get; init; }
    public required string StepId { get; init; }
    public required string OutcomeId { get; init; }
    public required string SkillId { get; init; }
    public required string Intent { get; init; }
    public required string RequestedCapability { get; init; }
    public string? ExpectedApplication { get; init; }
    public string? ExpectedProcess { get; init; }
    public string? ExpectedWindow { get; init; }
    public string? TargetIntent { get; init; }
    public ApplicationReadinessLevel RequiredReadiness { get; init; }
    public required string ExpectedEffect { get; init; }
    public required string VerificationPlan { get; init; }
    public required string IdempotencyKey { get; init; }
    public AgentRiskLevel RiskLevel { get; init; }
    public SideEffectClass SideEffectClass { get; init; }
    public IReadOnlyList<string> Constraints { get; init; } = [];
    public int WorldStateVersion { get; init; }
    public string? CheckpointId { get; init; }
}

public sealed record OutcomeMatchResult(
    bool Matched,
    string? FailureCode,
    string Reason,
    IReadOnlyList<string> Evidence);

public sealed record DurableTurnContext(
    string TaskId,
    string ConversationId,
    AgentTaskMode Mode,
    string PinnedContext,
    bool ReconciliationRequired);

public sealed record DurableRunRequest(
    string ConversationId,
    AgentTriggerType TriggerType,
    string UserIntent,
    string OwnerScope,
    string SecurityContextJson = "{}",
    string PermissionContextJson = "{}",
    string WorkspaceContextJson = "{}",
    string ModelContextJson = "{}",
    string? ParentRunId = null,
    int RecursionDepth = 0,
    int ChildRunBudget = 6,

    /// <summary>
    /// The work is already named down to the last argument, so there is nothing
    /// for the skill router to be ambiguous about.
    ///
    /// The clarification gate exists for a goal expressed in words, where two
    /// runtime skills can both plausibly be what the user meant. An MCP operation
    /// is not that: the connector, the method, and the arguments were all decided
    /// before the run was asked for. Sending it through the same gate stops the
    /// operation with a question nobody can answer, because there was never a
    /// choice to make.
    /// </summary>
    bool OperationIsFullySpecified = false);

public sealed record DurableRecoveryResult(
    string TaskId,
    AgentTaskStatus PreviousStatus,
    AgentTaskStatus CurrentStatus,
    ResumeSafetyStatus ResumeSafety,
    bool RequiresUser,
    string Reason);

public sealed record DurableActionPreparation(
    bool Proceed,
    string? FailureCode,
    string? FailureReason,
    TaskAction? Action,
    SideEffectClass SideEffectClass,
    bool RequiresInvariantApproval)
{
    public AgentActionRequest? Request { get; init; }
    public CapabilityRoute? Route { get; init; }
}

public sealed record DurableActionOutcome(
    AgentToolResult Result,
    DurableTurnContext Context);

public sealed record DurableCompletionDecision(
    bool Completed,
    AgentTaskStatus Status,
    string Message,
    IReadOnlyList<string> EvidenceIds);

public sealed record GoalInterpretation(
    bool IsActionable,
    AgentTaskMode Mode,
    GoalSpec? Goal,
    string? ClarificationQuestion);

public sealed record CapabilityRoute(
    bool Allowed,
    string SelectedCapability,
    IReadOnlyList<string> FallbackChain,
    string Reason,
    SideEffectClass SideEffectClass,
    bool RequiresApproval,
    string VerificationMethod)
{
    public IReadOnlyList<CapabilityCandidateScore> Candidates { get; init; } = [];
    public string? FailureCode { get; init; }
}

public sealed record CapabilityCandidateScore(
    string CapabilityId,
    double Score,
    bool Eligible,
    string Reason);

public sealed record AuthoritativeOutcomeView(
    string OutcomeId,
    string Description,
    string Status,
    bool Required,
    IReadOnlyList<string> Dependencies,
    int EvidenceCount);

public sealed record AuthoritativeStepView(
    string StepId,
    string? OutcomeId,
    string Description,
    string Status,
    int AttemptCount,
    string? FailureCode);

public sealed record AuthoritativePlanView(
    string TaskId,
    string Goal,
    string PlanId,
    int PlanVersion,
    string TaskStatus,
    string PlanStatus,
    string? CurrentMilestoneId,
    string? CurrentStepId,
    string? SelectedCapability,
    string? Readiness,
    string? Recovery,
    IReadOnlyList<AuthoritativeOutcomeView> Outcomes,
    IReadOnlyList<AuthoritativeStepView> Steps)
{
    public static AuthoritativePlanView From(
        DurableTaskSnapshot snapshot,
        TaskAction? latestAction = null) =>
        new(
            snapshot.Task.TaskId,
            snapshot.Goal.NormalizedGoal,
            snapshot.Plan.PlanId,
            snapshot.Plan.Version,
            snapshot.Task.Status.ToString(),
            snapshot.Plan.Status.ToString(),
            snapshot.Task.CurrentMilestoneId,
            snapshot.Task.CurrentStepId,
            latestAction?.ToolName,
            snapshot.WorldState?.Source is { Length: > 0 } source
                ? $"observed:{source}:v{snapshot.WorldState.Version}"
                : null,
            snapshot.Task.Status == AgentTaskStatus.Recovering
                ? latestAction?.RecoveryStrategy ?? latestAction?.FailureCode ?? "Recovering"
                : null,
            snapshot.Plan.Outcomes.Select(outcome => new AuthoritativeOutcomeView(
                outcome.OutcomeId,
                outcome.Description,
                outcome.Status.ToString(),
                outcome.Required,
                outcome.Dependencies,
                outcome.EvidenceIds.Count)).ToList(),
            snapshot.Plan.Milestones.OrderBy(milestone => milestone.Sequence)
                .SelectMany(milestone => milestone.Steps)
                .Select(step => new AuthoritativeStepView(
                    step.StepId,
                    step.OutcomeId,
                    step.Description,
                    step.Status.ToString(),
                    step.AttemptCount,
                    step.FailureCode)).ToList());
}

public sealed record RuntimeMetricSnapshot(
    long TasksCreated,
    long CheckpointsSaved,
    long ActionsDispatched,
    long ActionsVerified,
    long DuplicateActionsPrevented,
    long FalseSuccessPrevented,
    long ResumeReconciliations,
    long PlanRevisions);

public sealed record SkillExecutionContext(
    string TaskId,
    GoalSpec Goal,
    int PlanVersion,
    TaskMilestone CurrentMilestone,
    TaskStep CurrentStep,
    int WorldStateVersion,
    IReadOnlyList<CapabilityHealth> AvailableCapabilities,
    IReadOnlyList<string> Constraints,
    WorldStateSnapshot? Environment);

public sealed record SkillActionDecision(
    bool Allowed,
    string? FailureCode,
    string Reason,
    string ExpectedVerification,
    bool CompensationRequired,
    IReadOnlyList<string> RequiredObservations);
