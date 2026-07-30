namespace SranDesktop.Domain.Entities;

public enum AgentRiskLevel { Low, Medium, High, Critical }
public enum AgentTaskMode { SimpleAction, StructuredTask, LongHorizonTask, Conversation }
public enum AgentTriggerType { Chat, Scheduler, LiveTrigger, Subagent, Api, Resume }
public enum AgentTaskStatus
{
    Draft, WaitingForClarification, WaitingForApproval, Ready, Running, Paused,
    Recovering, VerificationPending, Completed, Failed, Cancelled, Abandoned,
    Created, Planning, AwaitingPermission, Executing, Observing, Verifying,
    FailedTransient, UnknownOutcome, Reconciling, Cancelling, Interrupted,
    Resuming, NeedsUser
}
public enum PlanStatus { Active, Superseded, Completed, Failed }
public enum MilestoneStatus { Pending, Ready, InProgress, VerificationPending, Verified, Failed, Blocked, Cancelled }
public enum TaskStepStatus
{
    Pending, Ready, InProgress, ExecutionPending, Executed, VerificationPending,
    Verified, Failed, Blocked, Skipped, Cancelled, Compensated
}
public enum OutcomeStatus
{
    Pending, Ready, InProgress, EffectPending, Verified, Failed, Blocked,
    Waived, Cancelled
}
public enum OutcomeKind
{
    ApplicationReady, OperationProcessed, StateObserved, TextValue,
    BrowserState, ProcessState, FileState, ConstraintPreserved, Generic
}
public enum DefinitionOfDoneStatus { Pending, Satisfied, Failed, NotVerifiable, WaivedByUser }
public enum SideEffectClass
{
    ReadOnly, ReversibleLowRisk, ReversibleMediumRisk, Consequential,
    Irreversible, SecuritySensitive, CredentialSensitive
}
public enum TaskActionStatus
{
    Planned, ApprovalPending, Authorized, Dispatching, Dispatched, ResultReceived,
    VerificationPending, Verified, Failed, UnknownOutcome, Compensated
}
public enum ResumeSafetyStatus { Safe, ReconciliationRequired, Blocked, Corrupted }
public enum EvidenceType
{
    ToolResult, ProcessExit, FileHash, FileMetadata, WindowState, DomState, UiaState,
    VisualState, UserConfirmation, WorldState, Verification, Artifact
}
public enum SensitiveDataClassification { Public, Internal, Personal, Confidential, Secret }
public enum CapabilityAvailability { Available, Degraded, Unavailable, Unknown }
public enum CapabilityHealthState { Healthy, Degraded, Unhealthy, Unknown }
public enum SkillExecutionStatus
{
    Resolved, PreconditionsPending, Ready, Running, VerificationPending,
    Verified, Failed, Recovering, Compensating, Paused
}
public enum ObservationTrust
{
    TrustedUserInstruction, SystemPolicy, TrustedConfiguration, ToolObservation,
    UntrustedExternalContent, SensitiveData, Secret, ExecutableInstruction
}
public enum CompensationStatus { Pending, Ready, InProgress, Verified, Failed, Conflict, NotReversible }
public enum DurableToolOutcome
{
    Succeeded,
    FailedNoEffect,
    FailedPartialEffect,
    FailedTransient,
    FailedPermanent,
    UnknownOutcome,
    Cancelled,
    CapabilityUnavailable
}
public enum RuntimeFailureClassification
{
    None,
    TransientNetwork,
    StaleState,
    PermissionDenied,
    InvalidArgument,
    PolicyDenied,
    ResourceUnavailable,
    TimeoutBeforeEffect,
    TimeoutAfterPossibleEffect,
    WorkerCrash,
    UnknownOutcome,
    Permanent
}
public enum VerificationDisposition { Verified, Failed, Inconclusive, NeedsUser }

/// <summary>
/// The authoritative transition policy for persisted runs. The store also
/// enforces this policy so callers cannot bypass it with arbitrary updates.
/// </summary>
public static class AgentRunStateMachine
{
    public static bool CanTransition(AgentTaskStatus from, AgentTaskStatus to)
    {
        if (from == to)
            return true;
        if (from is AgentTaskStatus.Completed or AgentTaskStatus.Failed
            or AgentTaskStatus.Cancelled or AgentTaskStatus.Abandoned)
            return false;
        if (to is AgentTaskStatus.Cancelled or AgentTaskStatus.Cancelling
            or AgentTaskStatus.Failed)
            return true;
        if (to == AgentTaskStatus.Interrupted)
            return true;

        return from switch
        {
            AgentTaskStatus.Draft or AgentTaskStatus.Created =>
                to is AgentTaskStatus.Planning or AgentTaskStatus.WaitingForClarification
                    or AgentTaskStatus.WaitingForApproval or AgentTaskStatus.Ready
                    or AgentTaskStatus.NeedsUser,
            AgentTaskStatus.Planning =>
                to is AgentTaskStatus.WaitingForClarification
                    or AgentTaskStatus.WaitingForApproval or AgentTaskStatus.Ready
                    or AgentTaskStatus.NeedsUser,
            AgentTaskStatus.WaitingForClarification or AgentTaskStatus.NeedsUser =>
                to is AgentTaskStatus.Planning or AgentTaskStatus.Ready,
            AgentTaskStatus.WaitingForApproval or AgentTaskStatus.AwaitingPermission =>
                to is AgentTaskStatus.Ready or AgentTaskStatus.Running
                    or AgentTaskStatus.Executing or AgentTaskStatus.NeedsUser,
            AgentTaskStatus.Ready =>
                to is AgentTaskStatus.Running or AgentTaskStatus.Executing
                    or AgentTaskStatus.VerificationPending or AgentTaskStatus.Verifying
                    or AgentTaskStatus.Paused or AgentTaskStatus.Interrupted
                    or AgentTaskStatus.Recovering or AgentTaskStatus.Completed,
            AgentTaskStatus.Running or AgentTaskStatus.Executing =>
                to is AgentTaskStatus.Running or AgentTaskStatus.Observing
                    or AgentTaskStatus.VerificationPending or AgentTaskStatus.Verifying
                    or AgentTaskStatus.Paused or AgentTaskStatus.Interrupted
                    or AgentTaskStatus.Recovering or AgentTaskStatus.FailedTransient
                    or AgentTaskStatus.UnknownOutcome or AgentTaskStatus.Completed,
            AgentTaskStatus.Observing or AgentTaskStatus.Verifying
                or AgentTaskStatus.VerificationPending =>
                to is AgentTaskStatus.Ready or AgentTaskStatus.Running
                    or AgentTaskStatus.Executing or AgentTaskStatus.Recovering
                    or AgentTaskStatus.Reconciling or AgentTaskStatus.Paused
                    or AgentTaskStatus.UnknownOutcome or AgentTaskStatus.Completed,
            AgentTaskStatus.FailedTransient =>
                to is AgentTaskStatus.Recovering or AgentTaskStatus.Ready
                    or AgentTaskStatus.NeedsUser,
            AgentTaskStatus.UnknownOutcome =>
                to is AgentTaskStatus.Reconciling or AgentTaskStatus.NeedsUser
                    or AgentTaskStatus.Paused,
            AgentTaskStatus.Paused or AgentTaskStatus.Interrupted =>
                to is AgentTaskStatus.Resuming or AgentTaskStatus.Recovering
                    or AgentTaskStatus.Reconciling or AgentTaskStatus.Ready
                    or AgentTaskStatus.Running,
            AgentTaskStatus.Resuming or AgentTaskStatus.Recovering
                or AgentTaskStatus.Reconciling =>
                to is AgentTaskStatus.Ready or AgentTaskStatus.Running
                    or AgentTaskStatus.Executing or AgentTaskStatus.VerificationPending
                    or AgentTaskStatus.Verifying or AgentTaskStatus.Paused
                    or AgentTaskStatus.NeedsUser or AgentTaskStatus.Completed
                    or AgentTaskStatus.Recovering,
            AgentTaskStatus.Cancelling => to == AgentTaskStatus.Cancelled,
            _ => false
        };
    }

    public static void EnsureTransition(AgentTaskStatus from, AgentTaskStatus to)
    {
        if (!CanTransition(from, to))
            throw new InvalidOperationException(
                $"Invalid durable run state transition: {from} -> {to}.");
    }
}

public sealed record DefinitionOfDoneItem
{
    public required string Id { get; init; }
    public required string Description { get; init; }
    public required string VerificationType { get; init; }
    public required string RequiredEvidence { get; init; }
    public bool Required { get; init; } = true;
    public DefinitionOfDoneStatus Status { get; set; } = DefinitionOfDoneStatus.Pending;
    public string? VerificationResult { get; set; }
    public List<string> EvidenceIds { get; init; } = [];
    public string? OutcomeId { get; init; }
}

public sealed record GoalSpec
{
    public required string GoalId { get; init; }
    public required string OriginalUserRequest { get; init; }
    public required string NormalizedGoal { get; set; }
    public required string DesiredOutcome { get; set; }
    public List<string> Constraints { get; init; } = [];
    public List<string> NonGoals { get; init; } = [];
    public List<string> Preferences { get; init; } = [];
    public List<string> Assumptions { get; init; } = [];
    public List<string> MissingInformation { get; init; } = [];
    public AgentRiskLevel RiskLevel { get; set; }
    public List<DefinitionOfDoneItem> DefinitionOfDone { get; init; } = [];
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public required DateTimeOffset UpdatedAtUtc { get; set; }
    public int Version { get; set; } = 1;
}

public sealed record AgentTask
{
    public required string TaskId { get; init; }
    public required string ConversationId { get; init; }
    public required string GoalId { get; init; }
    public AgentTaskMode Mode { get; init; }
    public AgentTaskStatus Status { get; set; }
    public int CurrentPlanVersion { get; set; }
    public string? CurrentMilestoneId { get; set; }
    public string? CurrentStepId { get; set; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public DateTimeOffset? StartedAtUtc { get; set; }
    public required DateTimeOffset UpdatedAtUtc { get; set; }
    public DateTimeOffset? PausedAtUtc { get; set; }
    public DateTimeOffset? CompletedAtUtc { get; set; }
    public string? FailureReason { get; set; }
    public int RetryBudget { get; init; } = 5;
    public int ActionBudget { get; init; } = 100;
    public TimeSpan TimeBudget { get; init; } = TimeSpan.FromMinutes(20);
    public int ModelCallBudget { get; init; } = 100;
    public AgentRiskLevel RiskLevel { get; init; }
    public int CheckpointVersion { get; set; }
    public required string OwnerScope { get; init; }
    public string? ParentRunId { get; init; }
    public AgentTriggerType TriggerType { get; init; } = AgentTriggerType.Chat;
    public string UserIntent { get; init; } = string.Empty;
    public string SecurityContextJson { get; init; } = "{}";
    public string PermissionContextJson { get; init; } = "{}";
    public string WorkspaceContextJson { get; init; } = "{}";
    public string ModelContextJson { get; init; } = "{}";
    public string? CurrentCheckpointId { get; set; }
    public DateTimeOffset? CancellationRequestedAtUtc { get; set; }
    public string? FailureSummary { get; set; }
    public string? FinalResultReference { get; set; }
    public int ChildRunBudget { get; init; } = 6;
    public int RecursionDepth { get; init; }
    public string? SkillId { get; set; }
    public string? SkillVersion { get; set; }
    public string? SkillExecutionId { get; set; }
    public int Version { get; set; } = 1;
}

public sealed record TaskPlan
{
    public required string PlanId { get; init; }
    public required string TaskId { get; init; }
    public int Version { get; init; }
    public int GoalVersion { get; init; }
    public required string PlanningReason { get; init; }
    public required string CreatedBy { get; init; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public string? SupersedesPlanId { get; init; }
    public PlanStatus Status { get; set; } = PlanStatus.Active;
    public List<string> Constraints { get; init; } = [];
    public List<TaskOutcome> Outcomes { get; init; } = [];
    public List<TaskMilestone> Milestones { get; init; } = [];
}

public sealed record TaskOutcome
{
    public required string OutcomeId { get; init; }
    public required string TaskId { get; init; }
    public string? ParentOutcomeId { get; init; }
    public required string Description { get; init; }
    public required string IntendedState { get; init; }
    public List<string> Dependencies { get; init; } = [];
    public bool Required { get; init; } = true;
    public bool Prohibited { get; init; }
    public OutcomeKind Kind { get; init; } = OutcomeKind.Generic;
    public List<string> VerificationRequirements { get; init; } = [];
    public List<string> EvidenceRequirements { get; init; } = [];
    public OutcomeStatus Status { get; set; } = OutcomeStatus.Pending;
    public List<string> ProducedArtifacts { get; init; } = [];
    public int SourceGoalVersion { get; init; }
    public string? ExpectedApplication { get; init; }
    public string? ExpectedProcess { get; init; }
    public string? ExpectedValue { get; init; }
    public List<string> EvidenceIds { get; init; } = [];
    public string? VerificationResult { get; set; }
}

public sealed record TaskMilestone
{
    public required string MilestoneId { get; init; }
    public required string TaskId { get; init; }
    public required string PlanId { get; init; }
    public required string Title { get; init; }
    public required string Objective { get; init; }
    public List<string> Dependencies { get; init; } = [];
    public List<string> Preconditions { get; init; } = [];
    public List<DefinitionOfDoneItem> DefinitionOfDone { get; init; } = [];
    public MilestoneStatus Status { get; set; } = MilestoneStatus.Pending;
    public List<string> EvidenceIds { get; init; } = [];
    public string? FailureReason { get; set; }
    public int Sequence { get; init; }
    public List<TaskStep> Steps { get; init; } = [];
}

public sealed record TaskStep
{
    public required string StepId { get; init; }
    public required string MilestoneId { get; init; }
    public required string TaskId { get; init; }
    public string? OutcomeId { get; init; }
    public required string Description { get; init; }
    public string? ParentStepId { get; init; }
    public string StepType { get; init; } = "tool";
    public string? ToolName { get; set; }
    public string? StructuredArgumentsReference { get; set; }
    public required string IntendedOutcome { get; init; }
    public List<string> Dependencies { get; init; } = [];
    public List<string> Preconditions { get; init; } = [];
    public List<string> PreferredCapabilities { get; init; } = [];
    public List<string> ExpectedEffects { get; init; } = [];
    public required string VerificationPlan { get; init; }
    public AgentRiskLevel RiskLevel { get; init; }
    public SideEffectClass SideEffectClass { get; init; }
    public required string IdempotencyPolicy { get; init; }
    public required string RetryPolicy { get; init; }
    public string? RollbackOrCompensation { get; init; }
    public TaskStepStatus Status { get; set; } = TaskStepStatus.Pending;
    public int AttemptCount { get; set; }
    public string? CurrentIdempotencyKey { get; set; }
    public DateTimeOffset? StartedAtUtc { get; set; }
    public DateTimeOffset? CompletedAtUtc { get; set; }
    public List<string> ObservationIds { get; init; } = [];
    public VerificationDisposition? VerificationDisposition { get; set; }
    public DurableToolOutcome? ToolOutcome { get; set; }
    public RuntimeFailureClassification? FailureClassification { get; set; }
    public List<string> EvidenceIds { get; init; } = [];
    public string? FailureCode { get; set; }
}

public sealed record TaskCheckpoint
{
    public required string CheckpointId { get; init; }
    public required string TaskId { get; init; }
    public int PlanVersion { get; init; }
    public string? CurrentMilestoneId { get; init; }
    public string? CurrentStepId { get; init; }
    public AgentTaskStatus TaskStatus { get; init; }
    public int WorldStateVersion { get; init; }
    public string? LastCompletedActionId { get; init; }
    public string? PendingActionId { get; init; }
    public required string ApprovalState { get; init; }
    public required string ContextInvariantSnapshot { get; init; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public ResumeSafetyStatus ResumeSafetyStatus { get; init; }
    public required string Checksum { get; init; }
    public int Version { get; init; }
}

public sealed record TaskEvidence
{
    public required string EvidenceId { get; init; }
    public required string TaskId { get; init; }
    public string? OutcomeId { get; init; }
    public string? StepId { get; init; }
    public string? ActionId { get; init; }
    public EvidenceType EvidenceType { get; init; }
    public required string Source { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
    public required string Summary { get; init; }
    public required string StructuredData { get; init; }
    public string? ArtifactReference { get; init; }
    public double Confidence { get; init; }
    public SensitiveDataClassification SensitiveDataClassification { get; init; }
    public required string RetentionPolicy { get; init; }
    public ObservationTrust TrustLevel { get; init; } = ObservationTrust.ToolObservation;
    public string ProvenanceJson { get; init; } = "{}";
    public List<string> AllowedUses { get; init; } = ["read", "summarize", "verify"];
    public List<string> ProhibitedUses { get; init; } =
        ["change_policy", "grant_permission", "select_external_destination"];
}

public sealed record TaskVerificationResult
{
    public required string VerificationId { get; init; }
    public required string TaskId { get; init; }
    public required string StepId { get; init; }
    public string? ActionId { get; init; }
    public VerificationDisposition Disposition { get; init; }
    public required string ExpectedState { get; init; }
    public required string ActualStateSummary { get; init; }
    public List<string> EvidenceIds { get; init; } = [];
    public string? SuggestedRecoveryAction { get; init; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
}

public sealed record WorldStateSnapshot
{
    public required string WorldStateId { get; init; }
    public required string TaskId { get; init; }
    public int Version { get; init; }
    public required DateTimeOffset ObservedAtUtc { get; init; }
    public required string Source { get; init; }
    public double Confidence { get; init; }
    public string? ActiveProcess { get; init; }
    public string? ForegroundWindow { get; init; }
    public List<string> OpenWindows { get; init; } = [];
    public List<string> RunningProcesses { get; init; } = [];
    public List<string> InstalledApplications { get; init; } = [];
    public string? CurrentProject { get; init; }
    public List<string> CurrentFiles { get; init; } = [];
    public string? CurrentUrl { get; init; }
    public List<string> BrowserTabs { get; init; } = [];
    public List<string> Dialogs { get; init; } = [];
    public List<string> Permissions { get; init; } = [];
    public string? NetworkState { get; init; }
    public string? DiskState { get; init; }
    public List<string> DependencyState { get; init; } = [];
    public bool PendingRestart { get; init; }
    public List<string> Artifacts { get; init; } = [];
    public List<string> Unknowns { get; init; } = [];
    public required string Provenance { get; init; }
    public DateTimeOffset? ExpiresAtUtc { get; init; }
}

public sealed record TaskAction
{
    public required string ActionId { get; init; }
    public required string TaskId { get; init; }
    public required string PlanId { get; init; }
    public required string MilestoneId { get; init; }
    public required string StepId { get; init; }
    public string? OutcomeId { get; init; }
    public required string ToolCallId { get; init; }
    public required string ToolName { get; init; }
    public required string ArgumentsFingerprint { get; init; }
    public required string IdempotencyKey { get; init; }
    public SideEffectClass SideEffectClass { get; init; }
    public TaskActionStatus Status { get; set; }
    public int Attempt { get; init; }
    public string? ParentActionId { get; init; }
    public string? RetryId { get; init; }
    public int ObservationVersion { get; init; }
    public string? RecoveryStrategy { get; init; }
    public string? CapabilityChangeReason { get; init; }
    public string? FailureCode { get; set; }
    public string? FailureReason { get; set; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public required DateTimeOffset UpdatedAtUtc { get; set; }
}

public sealed record TaskApproval
{
    public required string ApprovalId { get; init; }
    public required string TaskId { get; init; }
    public required string StepId { get; init; }
    public required string ToolCallId { get; init; }
    public required string ToolName { get; init; }
    public required string ActionFingerprint { get; init; }
    public required string Scope { get; init; }
    public required string Status { get; init; }
    public required DateTimeOffset ExpiresAtUtc { get; init; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
}

public sealed record TaskEvent
{
    public required string EventId { get; init; }
    public required string TaskId { get; init; }
    public required string EventType { get; init; }
    public string? PlanId { get; init; }
    public string? MilestoneId { get; init; }
    public string? StepId { get; init; }
    public string? OutcomeId { get; init; }
    public string? ActionId { get; init; }
    public string? ParentActionId { get; init; }
    public string? RetryId { get; init; }
    public string? ToolCallId { get; init; }
    public string? ModelRequestId { get; init; }
    public string? CaptureId { get; init; }
    public string? CheckpointId { get; init; }
    public int? WorldStateVersion { get; init; }
    public required DateTimeOffset TimestampUtc { get; init; }
    public required string DataJson { get; init; }
    public long Sequence { get; set; }
}

public sealed record DurableTaskSnapshot(
    GoalSpec Goal,
    AgentTask Task,
    TaskPlan Plan,
    TaskCheckpoint? Checkpoint,
    WorldStateSnapshot? WorldState);

public sealed record SkillDefinition
{
    public required string SkillId { get; init; }
    public required string Name { get; init; }
    public required string Version { get; init; }
    public required string Description { get; init; }
    public List<string> SupportedIntents { get; init; } = [];
    public List<string> RequiredCapabilities { get; init; } = [];
    public List<string> OptionalCapabilities { get; init; } = [];
    public List<string> Preconditions { get; init; } = [];
    public AgentRiskLevel RiskLevel { get; init; }
    public List<SideEffectClass> SideEffectClasses { get; init; } = [];
    public List<string> DefaultMilestones { get; init; } = [];
    public List<string> VerificationRequirements { get; init; } = [];
    public required string RecoveryPolicy { get; init; }
    public required string CompensationPolicy { get; init; }
    public List<string> RequiredApprovals { get; init; } = [];
    public List<string> SupportedPlatforms { get; init; } = [];
    public List<string> ProducedArtifacts { get; init; } = [];
    public required string TrustedSourcesPolicy { get; init; }
    public TimeSpan Timeout { get; init; } = TimeSpan.FromMinutes(20);
    public int RetryLimit { get; init; } = 3;
}

public sealed record SkillResolution
{
    public required string ResolutionId { get; init; }
    public required string TaskId { get; init; }
    public required SkillDefinition Skill { get; init; }
    public double Score { get; init; }
    public double ScoreGap { get; init; }
    public bool Ambiguous { get; init; }
    public List<string> CandidateSkillIds { get; init; } = [];
    public required string Reason { get; init; }
    public required DateTimeOffset ResolvedAtUtc { get; init; }
}

public sealed record CapabilityHealth
{
    public required string CapabilityId { get; init; }
    public required string Provider { get; init; }
    public string? Version { get; init; }
    public CapabilityAvailability Availability { get; set; } = CapabilityAvailability.Unknown;
    public CapabilityHealthState Health { get; set; } = CapabilityHealthState.Unknown;
    public AgentRiskLevel Risk { get; init; }
    public bool VerificationSupport { get; init; }
    public bool RollbackSupport { get; init; }
    public bool RequiresForeground { get; init; }
    public bool RequiresAdmin { get; init; }
    public bool RequiresNetwork { get; init; }
    public List<string> SupportedArtifacts { get; init; } = [];
    public string? LastFailure { get; set; }
    public long Attempts { get; set; }
    public long Failures { get; set; }
    public double FailureRate => Attempts == 0 ? 0 : (double)Failures / Attempts;
    public double AverageLatencyMs { get; set; }
    public required DateTimeOffset UpdatedAtUtc { get; set; }
}

public sealed record EnvironmentFact
{
    public required string FactId { get; init; }
    public required string Name { get; init; }
    public required string Value { get; init; }
    public required string Source { get; init; }
    public required DateTimeOffset ObservedAtUtc { get; init; }
    public double Confidence { get; init; }
    public bool Verified { get; init; }
    public ObservationTrust Trust { get; init; }
    public DateTimeOffset? ExpiresAtUtc { get; init; }
    public required string InvalidationRule { get; init; }
}

public sealed record SkillExecutionResult
{
    public required string SkillId { get; init; }
    public required string SkillVersion { get; init; }
    public required string ExecutionId { get; init; }
    public bool Started { get; init; }
    public bool Completed { get; init; }
    public bool Verified { get; init; }
    public List<string> ProducedArtifacts { get; init; } = [];
    public List<string> EvidenceIds { get; init; } = [];
    public List<string> SideEffects { get; init; } = [];
    public List<string> WorldStateChanges { get; init; } = [];
    public string? FailureCode { get; init; }
    public string? FailureReason { get; init; }
    public bool Retryable { get; init; }
    public bool CompensationRequired { get; init; }
    public string? ResumeToken { get; init; }
}

public sealed record CompensationLedgerEntry
{
    public required string CompensationId { get; init; }
    public required string TaskId { get; init; }
    public required string StepId { get; init; }
    public required string ActionId { get; init; }
    public required string Target { get; init; }
    public required string BeforeState { get; init; }
    public required string AfterState { get; init; }
    public bool Reversible { get; init; }
    public string? CompensationAction { get; init; }
    public CompensationStatus Status { get; set; }
    public List<string> EvidenceIds { get; init; } = [];
    public required DateTimeOffset UpdatedAtUtc { get; set; }
}

public sealed record DurableMetric
{
    public required string MetricId { get; init; }
    public required string TaskId { get; init; }
    public string? SkillId { get; init; }
    public required string Name { get; init; }
    public double Value { get; init; }
    public required string DimensionsJson { get; init; }
    public required DateTimeOffset TimestampUtc { get; init; }
}
