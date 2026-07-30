using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Interfaces;

public interface IAgentTaskStore
{
    Task InitializeAsync(CancellationToken ct = default);
    Task CreateAsync(
        GoalSpec goal,
        AgentTask task,
        TaskPlan plan,
        TaskCheckpoint checkpoint,
        CancellationToken ct = default);
    Task<DurableTaskSnapshot?> GetActiveByConversationAsync(
        string conversationId,
        CancellationToken ct = default);
    Task<DurableTaskSnapshot?> GetLatestByConversationAsync(
        string conversationId,
        CancellationToken ct = default);
    Task<DurableTaskSnapshot?> GetAsync(string taskId, CancellationToken ct = default);
    Task<IReadOnlyList<DurableTaskSnapshot>> GetIncompleteAsync(
        string? ownerScope = null,
        CancellationToken ct = default);
    Task<int> CountChildrenAsync(string parentRunId, CancellationToken ct = default);
    Task<bool> UpdateTaskAsync(
        AgentTask task,
        int expectedVersion,
        CancellationToken ct = default);
    Task SaveGoalAsync(GoalSpec goal, CancellationToken ct = default);
    Task SavePlanAsync(TaskPlan plan, CancellationToken ct = default);
    Task SaveStepAsync(TaskStep step, CancellationToken ct = default);
    Task SaveCheckpointAsync(TaskCheckpoint checkpoint, CancellationToken ct = default);
    Task<TaskCheckpoint?> GetLatestCheckpointAsync(string taskId, CancellationToken ct = default);
    Task<bool> TryCreateActionAsync(TaskAction action, CancellationToken ct = default);
    Task<bool> CommitActionPreparationAsync(
        AgentTask task,
        int expectedTaskVersion,
        TaskStep step,
        TaskAction action,
        TaskCheckpoint checkpoint,
        TaskEvent taskEvent,
        CancellationToken ct = default);
    Task<bool> CommitActionOutcomeAsync(
        AgentTask task,
        int expectedTaskVersion,
        TaskPlan plan,
        TaskStep step,
        TaskAction action,
        TaskEvidence evidence,
        WorldStateSnapshot worldState,
        TaskCheckpoint checkpoint,
        IReadOnlyList<TaskEvent> events,
        CancellationToken ct = default);
    Task<TaskAction?> GetActionByIdempotencyKeyAsync(
        string taskId,
        string idempotencyKey,
        CancellationToken ct = default);
    Task<TaskAction?> GetActionAsync(string actionId, CancellationToken ct = default);
    Task<TaskAction?> GetLatestActionForStepAsync(
        string taskId,
        string stepId,
        CancellationToken ct = default);
    /// <summary>All actions recorded for a step, oldest first — used to detect a
    /// capability that keeps failing on the same step (anti-churn).</summary>
    Task<IReadOnlyList<TaskAction>> GetActionsForStepAsync(
        string taskId,
        string stepId,
        CancellationToken ct = default);
    Task UpdateActionAsync(TaskAction action, CancellationToken ct = default);
    Task SaveApprovalAsync(TaskApproval approval, CancellationToken ct = default);
    Task<TaskApproval?> GetValidApprovalAsync(
        string taskId,
        string stepId,
        string actionFingerprint,
        DateTimeOffset atUtc,
        CancellationToken ct = default);
    Task<bool> HasGrantedApprovalAsync(
        string taskId,
        DateTimeOffset atUtc,
        CancellationToken ct = default);
    Task AddEvidenceAsync(TaskEvidence evidence, CancellationToken ct = default);
    Task SaveVerificationAsync(
        TaskVerificationResult verification,
        CancellationToken ct = default);
    Task<IReadOnlyList<TaskEvidence>> GetEvidenceAsync(
        string taskId,
        CancellationToken ct = default);
    Task SaveWorldStateAsync(WorldStateSnapshot snapshot, CancellationToken ct = default);
    Task AppendEventAsync(TaskEvent taskEvent, CancellationToken ct = default);
    Task<IReadOnlyList<TaskEvent>> GetEventsAsync(string taskId, CancellationToken ct = default);
    Task SaveCapabilityHealthAsync(
        CapabilityHealth capability,
        CancellationToken ct = default);
    Task<IReadOnlyList<CapabilityHealth>> GetCapabilityHealthAsync(
        CancellationToken ct = default);
    Task SaveMetricAsync(DurableMetric metric, CancellationToken ct = default);
    Task<IReadOnlyList<DurableMetric>> GetMetricsAsync(
        string taskId,
        CancellationToken ct = default);
    Task SaveCompensationAsync(
        CompensationLedgerEntry entry,
        CancellationToken ct = default);
    Task<IReadOnlyList<CompensationLedgerEntry>> GetCompensationsAsync(
        string taskId,
        CancellationToken ct = default);
}
