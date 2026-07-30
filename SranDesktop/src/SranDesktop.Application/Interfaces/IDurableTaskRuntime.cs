using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Interfaces;

public interface IDurableTaskRuntime
{
    Task<DurableTurnContext> CreateRunAsync(
        DurableRunRequest request,
        CancellationToken ct = default);
    Task<DurableTurnContext> CreateChildRunAsync(
        string parentRunId,
        DurableRunRequest request,
        CancellationToken ct = default);
    Task<DurableTurnContext?> StartOrResumeAsync(
        string conversationId,
        IReadOnlyList<GatewayMessage> messages,
        string ownerScope,
        CancellationToken ct = default);
    Task<DurableActionPreparation> PrepareActionAsync(
        DurableTurnContext context,
        string toolCallId,
        string toolName,
        ToolCategory category,
        string argumentsJson,
        CancellationToken ct = default);
    Task RecordApprovalAsync(
        DurableTurnContext context,
        string toolCallId,
        string toolName,
        string argumentsJson,
        string scope,
        bool granted,
        DateTimeOffset expiresAtUtc,
        CancellationToken ct = default);
    Task<DurableActionOutcome> RecordResultAsync(
        DurableTurnContext context,
        DurableActionPreparation preparation,
        ToolResult result,
        long durationMs,
        CancellationToken ct = default);
    Task<DurableTurnContext> ApplyPlanUpdateAsync(
        DurableTurnContext context,
        string argumentsJson,
        CancellationToken ct = default);
    Task<DurableCompletionDecision> EvaluateCompletionAsync(
        DurableTurnContext context,
        CancellationToken ct = default);
    Task CompleteConversationAsync(
        DurableTurnContext context,
        string finalResultReference,
        CancellationToken ct = default);
    Task PauseAsync(
        DurableTurnContext context,
        string reason,
        CancellationToken ct = default);
    Task CancelAsync(
        string taskId,
        string reason,
        CancellationToken ct = default);
    Task<IReadOnlyList<DurableRecoveryResult>> RecoverInterruptedRunsAsync(
        string? ownerScope = null,
        CancellationToken ct = default);
    Task<IReadOnlyList<TaskEvent>> GetEventsAsync(
        string taskId,
        long afterSequence = 0,
        CancellationToken ct = default);
    Task<DurableTurnContext> RecordUnknownOutcomeAsync(
        DurableTurnContext context,
        DurableActionPreparation preparation,
        string reason,
        CancellationToken ct = default);
    Task<string> ExportMetricsAsync(
        string taskId,
        string format,
        CancellationToken ct = default);
    Task<AuthoritativePlanView> GetAuthoritativePlanAsync(
        string taskId,
        CancellationToken ct = default);
    RuntimeMetricSnapshot GetMetrics();
}
