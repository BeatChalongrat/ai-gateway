using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Durable record of what the runtime was about to ask an MCP server to do,
/// written before any byte leaves. It survives a process restart because that is
/// the only case that matters: if the host dies between "dispatched" and
/// "answered", this file is the sole reason a mutation is reconciled instead of
/// repeated.
/// </summary>
public interface IMcpDispatchJournal
{
    Task<McpDispatchIntent> RecordAsync(McpDispatchIntent intent, CancellationToken ct = default);

    Task ResolveAsync(
        string intentId,
        McpOperationStatus resolution,
        string reason,
        string? returnedObjectId = null,
        CancellationToken ct = default);

    Task UpdateAsync(McpDispatchIntent intent, CancellationToken ct = default);

    /// <summary>Intents whose outcome was never established. After a restart this
    /// is the reconciliation work list.</summary>
    Task<IReadOnlyList<McpDispatchIntent>> GetOpenAsync(
        string? connectorId = null,
        CancellationToken ct = default);

    /// <summary>An open intent with the same fingerprint means "this exact
    /// mutation may already be in flight or already done" — the guard that keeps
    /// duplicate side effects at zero across a restart.</summary>
    Task<McpDispatchIntent?> FindOpenByFingerprintAsync(
        string connectorId,
        string argumentsFingerprint,
        CancellationToken ct = default);

    Task<McpDispatchIntent?> GetAsync(string intentId, CancellationToken ct = default);
}

/// <summary>
/// The one door to an MCP server.
///
/// Nothing else in the application may talk to a connector: every operation goes
/// AgentRun → AgentStep → PrepareAction → trust → recorded intent → isolated
/// dispatch → observation → verification → RecordResult. Bypassing it would put
/// a side effect in the world with no durable step behind it, which is precisely
/// the failure this phase exists to remove.
/// </summary>
public interface IMcpDurableGateway
{
    /// <summary>Runs the operation under an existing durable run.</summary>
    Task<McpOperationResult> ExecuteAsync(
        DurableTurnContext context,
        McpOperationRequest request,
        CancellationToken ct = default);

    /// <summary>Runs the operation under a run the gateway owns — connector
    /// lifecycle work (dial, tools/list refresh, post-restart reconciliation)
    /// that no chat turn asked for but which still must be durable.</summary>
    Task<McpOperationResult> ExecuteOwnedAsync(
        McpOperationRequest request,
        string ownerScope,
        CancellationToken ct = default);

    /// <summary>
    /// Runs the operation under the AgentStep the caller has already prepared —
    /// the chat path, where the orchestrator owns PrepareAction/RecordResult for
    /// the model's tool call. Preparing a second action would record the same
    /// operation twice and spend the step's budget twice.
    ///
    /// With no durable run ambient it falls back to <see
    /// cref="ExecuteOwnedAsync"/>, so no MCP operation can happen without a step.
    /// </summary>
    Task<McpOperationResult> ExecuteAttachedAsync(
        McpOperationRequest request,
        string ownerScope,
        CancellationToken ct = default);

    /// <summary>Resolves every intent left open by an earlier process. Called at
    /// startup, before any new work is dispatched to the same connector.</summary>
    Task<IReadOnlyList<McpOperationResult>> ReconcileOpenIntentsAsync(
        string? connectorId = null,
        CancellationToken ct = default);
}
