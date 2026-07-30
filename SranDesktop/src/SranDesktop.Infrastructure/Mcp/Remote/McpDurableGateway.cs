using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// The single door between this application and any MCP server.
///
/// Every operation — a tools/list refresh as much as a mutation — travels the
/// same road:
///
///   AgentRun → AgentStep (PrepareAction) → trust validation → recorded dispatch
///   intent → isolated dispatch → observation → verification / reconciliation →
///   RecordResult → completion or NeedsUser
///
/// The ordering is the point. PrepareAction comes first so that even a refusal
/// leaves a durable step; the intent is written before any byte leaves so a
/// process that dies mid-call still knows what to ask about afterwards; and
/// RecordResult is reached on every path, including the ones that failed, so a
/// run never silently forgets that it touched the outside world.
/// </summary>
public sealed class McpDurableGateway : IMcpDurableGateway
{
    /// <summary>Owner scope used for connector work no chat turn asked for.</summary>
    public const string LifecycleOwnerScope = "connector:lifecycle";

    private readonly IDurableTaskRuntime _runtime;
    private readonly IMcpDispatchJournal _journal;
    private readonly IMcpConnectorSessionProvider _sessions;
    private readonly IMcpMutationReconciler _reconciler;
    private readonly ConnectorInstallationPolicy _installation;
    private readonly IConversationStore? _conversations;
    private readonly TimeSpan _operationTimeout;
    private readonly ILogger? _logger;

    /// <summary>
    /// How long one dispatched operation may go unanswered.
    ///
    /// A server that accepts a request and then says nothing is not a
    /// hypothetical: it is what a crashed handler, a deadlocked backend, or a
    /// deliberately hostile connector looks like from here, and the stdio
    /// transport has no timeout of its own. Without this the run waits forever,
    /// which is worse than either outcome it is waiting between — and a timeout
    /// AFTER the request went out is precisely an unknown outcome, so it lands in
    /// reconciliation rather than in a retry.
    /// </summary>
    public static TimeSpan DefaultOperationTimeout { get; } = TimeSpan.FromMinutes(2);

    public McpDurableGateway(
        IDurableTaskRuntime runtime,
        IMcpDispatchJournal journal,
        IMcpConnectorSessionProvider sessions,
        IMcpMutationReconciler? reconciler = null,
        ConnectorInstallationPolicy? installation = null,
        IConversationStore? conversations = null,
        TimeSpan? operationTimeout = null,
        ILogger? logger = null)
    {
        _operationTimeout = operationTimeout is { TotalMilliseconds: > 0 } configured
            ? configured
            : DefaultOperationTimeout;
        _runtime = runtime;
        _journal = journal;
        _sessions = sessions;
        _reconciler = reconciler ?? new McpMutationReconciler(logger);
        _installation = installation ?? ConnectorInstallationPolicy.Default;
        _conversations = conversations;
        _logger = logger;
    }

    public async Task<McpOperationResult> ExecuteAsync(
        DurableTurnContext context,
        McpOperationRequest request,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(context);
        ArgumentNullException.ThrowIfNull(request);

        var toolCallId = request.ToolCallId ?? $"mcp-{Guid.NewGuid():N}";
        var preparation = await _runtime.PrepareActionAsync(
            context,
            toolCallId,
            request.CapabilityName,
            ToolCategory.Network,
            request.ArgumentsJson,
            ct);
        if (!preparation.Proceed)
        {
            // A feature this build does not implement gets the same answer every
            // time. Left to the durable gate, repeated attempts degrade the
            // capability's health and the refusal turns into "no healthy
            // capability is available" — which sends the reader looking for an
            // outage instead of telling them the feature does not exist. The step
            // still exists either way; only the explanation changes.
            var unsupported = !McpProtocolFeatureMatrix.IsImplemented(request.Operation);

            // The refusal is already a durable fact: PrepareAction recorded it.
            // The ActionId travels with the result so a caller — or an audit —
            // can find that record. Omitting it made a refused operation look
            // stepless from the outside, which is indistinguishable from the
            // bypass this gateway exists to prevent.
            return new McpOperationResult
            {
                Status = unsupported
                    ? McpOperationStatus.CapabilityUnavailable
                    : McpOperationStatus.Blocked,
                ConnectorId = request.ConnectorId,
                Operation = request.Operation,
                FailureCode = unsupported ? "CapabilityUnavailable" : preparation.FailureCode,
                FailureReason = unsupported
                    ? McpProtocolFeatureMatrix.UnsupportedReason(request.Operation)
                    : preparation.FailureReason,
                RunId = context.TaskId,
                ActionId = preparation.Action?.ActionId,
                Phase = McpDispatchPhase.Prepared,
                Evidence = unsupported
                    ? ["feature_matrix=unsupported", $"operation={request.Operation}"]
                    : ["durable_gate=blocked", $"failure_code={preparation.FailureCode}"]
            };
        }

        var actionId = preparation.Action?.ActionId ?? "";
        var start = DateTimeOffset.UtcNow;
        var (result, toolResult) = await RunPipelineAsync(
            context.TaskId, actionId, toolCallId, request, ct);

        try
        {
            await _runtime.RecordResultAsync(
                context,
                preparation,
                toolResult,
                (long)(DateTimeOffset.UtcNow - start).TotalMilliseconds,
                ct);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException)
        {
            _logger?.LogError(ex,
                "Durable result commit failed for MCP {Operation} on {Connector}",
                request.Operation, request.ConnectorId);
            return result with
            {
                Status = McpOperationStatus.NeedsUser,
                FailureCode = "DurableStateCommitFailed",
                FailureReason =
                    "The operation may have executed, but its durable result could not be "
                    + "committed. Reconcile the connector's state before retrying.",
                Retryable = false
            };
        }

        if (result.Status == McpOperationStatus.NeedsUser)
            await _runtime.PauseAsync(
                context,
                result.FailureReason ?? "An MCP operation needs a person to resolve it.",
                ct);
        return result;
    }

    public async Task<McpOperationResult> ExecuteOwnedAsync(
        McpOperationRequest request,
        string ownerScope,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        // Connector lifecycle work is still work: it gets its own run so the
        // dial, the tools/list, and any reconciliation are steps somebody can
        // audit, not invisible background traffic.
        var conversationId = $"mcp:{request.ConnectorId}:{Guid.NewGuid():N}";
        if (_conversations is not null)
            // A run hangs off a conversation; lifecycle work needs one of its own
            // so it shows up as auditable history rather than an orphan row.
            await _conversations.UpsertConversationAsync(new Conversation
            {
                Id = conversationId,
                Title = $"Connector: {request.ConnectorId}",
                ModelId = "none",
                CreatedAt = DateTimeOffset.UtcNow,
                UpdatedAt = DateTimeOffset.UtcNow,
                Source = ConversationSource.Local
            }, ct);
        var context = await _runtime.CreateRunAsync(
            new DurableRunRequest(
                conversationId,
                AgentTriggerType.Api,
                DescribeIntent(request),
                ownerScope,
                SecurityContextJson: JsonSerializer.Serialize(new
                {
                    connector_id = request.ConnectorId,
                    operation = request.Operation.ToString(),
                    unattended = true,
                    consent_fallback = "deny"
                }),
                PermissionContextJson:
                    """{"unattended":true,"allow_stored_scope_only":true}""",
                // The connector, the method, and the arguments were all decided
                // before this call; there is no skill for the router to be
                // ambiguous about.
                OperationIsFullySpecified: true),
            ct);
        return await ExecuteAsync(context, request, ct);
    }

    /// <summary>
    /// Runs an operation whose AgentStep already exists — the chat path, where
    /// the orchestrator prepared the action for the model's tool call and will
    /// commit its result. Preparing a second action here would double-count the
    /// step against the run's budgets and record the same operation twice.
    ///
    /// If no durable run is ambient the operation falls back to
    /// <see cref="ExecuteOwnedAsync"/>, so there is no path on which an MCP call
    /// happens without a step behind it.
    /// </summary>
    public async Task<McpOperationResult> ExecuteAttachedAsync(
        McpOperationRequest request,
        string ownerScope,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        var runId = AgentRunExecutionContext.CurrentRunId;
        var toolCallId = AgentRunExecutionContext.CurrentStepId ?? request.ToolCallId;
        if (runId is not { Length: > 0 } || toolCallId is not { Length: > 0 })
            return await ExecuteOwnedAsync(request, ownerScope, ct);

        var actionId = await ResolveActionIdAsync(runId, toolCallId, ct);
        var (result, _) = await RunPipelineAsync(runId, actionId, toolCallId, request, ct);
        return result;
    }

    public async Task<IReadOnlyList<McpOperationResult>> ReconcileOpenIntentsAsync(
        string? connectorId = null,
        CancellationToken ct = default)
    {
        var open = await _journal.GetOpenAsync(connectorId, ct);
        var results = new List<McpOperationResult>();
        foreach (var intent in open)
        {
            // Reads left open are simply abandoned: repeating them proves
            // nothing about a side effect that never existed.
            if (intent.Risk is McpRiskClass.ReadOnly or McpRiskClass.IdempotentRead)
            {
                await _journal.ResolveAsync(
                    intent.IntentId,
                    McpOperationStatus.FailedNoEffect,
                    "A read left open by an interrupted process has no side effect to resolve.",
                    ct: ct);
                results.Add(Result(intent, McpOperationStatus.FailedNoEffect,
                    "InterruptedRead",
                    "The read was interrupted; nothing changed.",
                    ["reconcile=read_discarded"]));
                continue;
            }
            results.Add(await ReconcileIntentAsync(intent, ct));
        }
        return results;
    }

    // ── The pipeline ────────────────────────────────────────────────────────

    private async Task<(McpOperationResult Result, ToolResult ToolResult)> RunPipelineAsync(
        string runId,
        string actionId,
        string toolCallId,
        McpOperationRequest request,
        CancellationToken ct)
    {
        // 1. Does this build implement the feature at all? An unsupported one is
        //    named and refused, never quietly skipped.
        if (!McpProtocolFeatureMatrix.IsImplemented(request.Operation))
            return Terminal(
                runId, actionId, request, McpOperationStatus.CapabilityUnavailable,
                "CapabilityUnavailable",
                McpProtocolFeatureMatrix.UnsupportedReason(request.Operation),
                ["feature_matrix=unsupported", $"operation={request.Operation}"]);

        // 2. Trust. Re-derived before anything is launched, every single time —
        //    an approval granted once is not a licence for whatever binary now
        //    sits at that path. The refusal is NAMED: "never approved", "the
        //    binary changed", and "it now wants the network" are different
        //    problems with different remedies.
        var resolution = _sessions.Resolve(request.ConnectorId);
        if (!resolution.Resolved)
            return Terminal(
                runId, actionId, request, McpOperationStatus.Blocked,
                resolution.DenialCode ?? "ConnectorUntrusted",
                resolution.DenialReason ?? "The connector may not be launched.",
                [
                    $"trust_state={resolution.State}",
                    $"trust_code={resolution.DenialCode}",
                    resolution.PermissionExpansions.Count > 0
                        ? $"permission_expansion={string.Join(",", resolution.PermissionExpansions)}"
                        : "permission_expansion=none"
                ]);

        var manifest = resolution.Descriptor!.Manifest;

        // The launch-time policy runs again over the resolved manifest. The
        // resolver and this gate must not be able to disagree about whether a
        // connector may run depending on which one was asked.
        var verdict = ConnectorTrustPolicy.Evaluate(manifest, _installation);
        if (!verdict.MayLaunch)
            return Terminal(
                runId, actionId, request, McpOperationStatus.Blocked,
                verdict.RejectionCode ?? "ConnectorUntrusted",
                verdict.Reason ?? "The connector may not be launched.",
                [$"trust_state={verdict.State}", $"trust_code={verdict.RejectionCode}"]);

        // 3. Risk, from the approved connector policy — never from what the
        //    server says about itself.
        var riskPolicy = resolution.Descriptor!.RiskPolicy;
        var toolName = request.ToolName ?? request.Method;
        var assessment = McpRiskPolicy.Classify(riskPolicy, toolName);
        var fingerprint = McpDispatchIntent.Fingerprint(
            request.ConnectorId, toolName, request.ArgumentsJson);

        // 4. Duplicate guard. An open intent with this fingerprint means an
        //    earlier attempt may already have taken effect; the answer is to find
        //    out, not to try again.
        if (assessment.RequiresReconciliation)
        {
            var inflight = await _journal.FindOpenByFingerprintAsync(
                request.ConnectorId, fingerprint, ct);
            if (inflight is not null)
            {
                var reconciled = await ReconcileIntentAsync(inflight, ct);
                return (reconciled with { RunId = runId, ActionId = actionId },
                    ToToolResult(reconciled));
            }
        }

        // 5. Record the intent BEFORE dispatch.
        var intent = new McpDispatchIntent
        {
            IntentId = $"mcpintent-{Guid.NewGuid():N}",
            RunId = runId,
            ActionId = actionId,
            ToolCallId = toolCallId,
            ConnectorId = request.ConnectorId,
            Operation = request.Operation,
            ToolName = toolName,
            Risk = assessment.Risk,
            ArgumentsFingerprint = fingerprint,
            IdempotencyKey = $"{runId}:{actionId}:{fingerprint}",
            CreatedUtc = DateTimeOffset.UtcNow,
            VerificationTool = McpRiskPolicy.ResolveVerificationTool(riskPolicy, toolName),
            Phase = McpDispatchPhase.IntentRecorded
        };
        await _journal.RecordAsync(intent, ct);

        // 6. Dispatch, isolated.
        McpConnectorSession session;
        try
        {
            // A reconnect must really tear the session down and initialize a new
            // one — reusing the live session would report a "reconnect" that
            // never happened.
            session = request.Operation == McpOperationKind.Reconnect
                ? await _sessions.ReconnectAsync(request.ConnectorId, ct)
                : await _sessions.AcquireAsync(request.ConnectorId, ct);
        }
        catch (McpAuthorizationException ex)
        {
            // The credential was refused. That is not a transport fault to retry —
            // it is an account that needs reconnecting, and saying so is the
            // difference between a user who can fix it and one who waits.
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.FailedNoEffect,
                "The connector refused the credential.", ct: ct);
            return Terminal(
                runId, actionId, request, McpOperationStatus.Blocked,
                "ConnectorAuthorizationFailed", ex.Message,
                ["dispatch=not_attempted", "auth=refused"], assessment.Risk,
                retryable: false, intentId: intent.IntentId);
        }
        catch (Exception ex) when (ex is McpProtocolException or IOException
                                       or SandboxUnavailableException
                                       or InvalidOperationException
                                       // A remote connector fails as an HTTP or
                                       // socket error, and those escaping the
                                       // gateway would crash an unattended run
                                       // rather than leaving a durable step.
                                       or HttpRequestException
                                       or System.Net.Sockets.SocketException
                                       or TaskCanceledException)
        {
            // Nothing was dispatched, so nothing happened.
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.FailedNoEffect,
                $"The connector session could not be established: {ex.Message}", ct: ct);
            return Terminal(
                runId, actionId, request, McpOperationStatus.FailedNoEffect,
                "ConnectorUnavailable", ex.Message,
                ["dispatch=not_attempted"], assessment.Risk,
                retryable: assessment.MayRetryBeforeDispatch, intentId: intent.IntentId);
        }

        if (RequiredCapabilityMissing(session, request) is { } missing)
        {
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.CapabilityUnavailable,
                missing, ct: ct);
            return Terminal(
                runId, actionId, request, McpOperationStatus.CapabilityUnavailable,
                "CapabilityUnavailable", missing,
                ["capability=not_declared"], assessment.Risk, intentId: intent.IntentId);
        }

        await _journal.UpdateAsync(
            intent with { Phase = McpDispatchPhase.Dispatched }, ct);

        try
        {
            var payload = await DispatchWithinDeadlineAsync(session, request, intent, ct);

            // 7. Observation. Everything a connector returns is data, never
            //    instructions — wrapped here so no caller has to remember to.
            var observed = UntrustedObservationBoundary.ProtectIfUntrusted(
                $"mcp_{request.Operation}".ToLowerInvariant(), payload.Content);

            // 8. Verification: an answer only counts if it came back from the
            //    server we dispatched to.
            if (!string.Equals(session.ServerIdentity, payload.ServerIdentity,
                    StringComparison.Ordinal))
            {
                await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.NeedsUser,
                    "The answering server's identity changed mid-operation.", ct: ct);
                return Terminal(
                    runId, actionId, request, McpOperationStatus.NeedsUser,
                    "ProvenanceMismatch",
                    "The answer came from a server whose identity does not match the one "
                    + "the request was sent to.",
                    ["provenance=mismatch"], assessment.Risk, intentId: intent.IntentId);
            }

            if (payload.IsError)
            {
                // The server answered — we know the outcome, it just is not a
                // good one. That is a clean failure, not an unknown.
                await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.FailedNoEffect,
                    "The server reported an error, so the outcome is known.", ct: ct);
                return Terminal(
                    runId, actionId, request, McpOperationStatus.FailedNoEffect,
                    "McpToolError", observed,
                    ["dispatch=answered", "outcome=server_error"],
                    assessment.Risk, retryable: true, intentId: intent.IntentId,
                    content: observed);
            }

            await _journal.ResolveAsync(
                intent.IntentId,
                McpOperationStatus.Succeeded,
                "The server answered the request that was dispatched.",
                payload.ObjectId,
                ct);
            var success = new McpOperationResult
            {
                Status = McpOperationStatus.Succeeded,
                ConnectorId = request.ConnectorId,
                Operation = request.Operation,
                Content = observed,
                RunId = runId,
                ActionId = actionId,
                IntentId = intent.IntentId,
                Risk = assessment.Risk,
                Phase = McpDispatchPhase.Recorded,
                Evidence =
                [
                    $"connector={request.ConnectorId}",
                    $"operation={request.Operation}",
                    $"risk={assessment.Risk}",
                    $"risk_policy_validated={assessment.PolicyValidated}",
                    $"generation={session.Generation}",
                    $"server_identity={session.ServerIdentity}",
                    $"intent={intent.IntentId}",
                    $"action={actionId}",
                    "provenance=verified"
                ]
            };
            return (success, ToToolResult(success));
        }
        catch (McpDispatchException ex)
        {
            return await ClassifyDispatchFailureAsync(
                runId, actionId, request, intent, assessment, ex, ct);
        }
        catch (McpAuthorizationException ex)
        {
            // Refused mid-session: the credential expired or was revoked between
            // acquiring the session and using it. Nothing was executed.
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.FailedNoEffect,
                "The connector refused the credential.", ct: ct);
            return Terminal(
                runId, actionId, request, McpOperationStatus.Blocked,
                "ConnectorAuthorizationFailed", ex.Message,
                ["dispatch=refused", "auth=refused"], assessment.Risk,
                retryable: false, intentId: intent.IntentId);
        }
        catch (Exception ex) when (ex is McpProtocolException or JsonException
                                       // A remote connection that dies mid-call
                                       // reaches us as one of these, and the
                                       // request had already left.
                                       or HttpRequestException
                                       or System.Net.Sockets.SocketException
                                       or TaskCanceledException)
        {
            // The failure happened after the request went out: the server may
            // still have acted on it.
            return await ClassifyDispatchFailureAsync(
                runId, actionId, request, intent, assessment,
                new McpDispatchException(DispatchStage.Dispatched, ex.Message, ex), ct);
        }
    }

    /// <summary>
    /// The distinction the whole phase turns on. A request that never left the
    /// queue provably did nothing. One that reached the server may already have
    /// created the record — and for anything that is not a read, the only honest
    /// next move is to go and look, never to try again.
    /// </summary>
    private async Task<(McpOperationResult, ToolResult)> ClassifyDispatchFailureAsync(
        string runId,
        string actionId,
        McpOperationRequest request,
        McpDispatchIntent intent,
        McpRiskAssessment assessment,
        McpDispatchException failure,
        CancellationToken ct)
    {
        var evidence = new List<string>
        {
            $"dispatch_stage={failure.Stage}",
            $"risk={assessment.Risk}",
            $"risk_policy_validated={assessment.PolicyValidated}",
            $"intent={intent.IntentId}",
            $"action={actionId}"
        };

        if (!failure.OutcomeUnknown)
        {
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.FailedNoEffect,
                "The request never reached the server.", ct: ct);
            return Terminal(
                runId, actionId, request, McpOperationStatus.FailedNoEffect,
                "McpDispatchFailedNoEffect", failure.Message,
                [.. evidence, "effect=none"],
                assessment.Risk, retryable: assessment.MayRetryBeforeDispatch,
                intentId: intent.IntentId);
        }

        if (!assessment.RequiresReconciliation)
        {
            // A read whose answer was lost: repeating it is harmless, but only
            // against a server we can still identify as the same one.
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.FailedNoEffect,
                "A read lost its answer; reads have no side effect to reconcile.", ct: ct);
            return Terminal(
                runId, actionId, request, McpOperationStatus.FailedNoEffect,
                "McpReadLostAnswer",
                "The read did not return an answer. Reads have no side effect, so it may "
                + "be repeated once the connector is reachable again.",
                [.. evidence, "effect=none_read", "retry=allowed_after_provenance_check"],
                assessment.Risk, retryable: assessment.MayRetryAfterDispatch,
                intentId: intent.IntentId);
        }

        // A mutation with an unknown outcome. Reconnect, ask the server what it
        // actually has, and never redispatch.
        var reconciled = await ReconcileIntentAsync(
            intent with { Phase = McpDispatchPhase.Dispatched }, ct);
        return (reconciled with { RunId = runId, ActionId = actionId },
            ToToolResult(reconciled));
    }

    /// <summary>
    /// Resolves an open mutation intent by reading the server's real state:
    /// reconnect/reinitialize, look the object up by id or by the idempotency
    /// fingerprint that travelled with the original call, and complete WITHOUT
    /// dispatching the mutation again. When nothing can establish the truth the
    /// answer is a person, not a guess.
    /// </summary>
    private async Task<McpOperationResult> ReconcileIntentAsync(
        McpDispatchIntent intent,
        CancellationToken callerToken)
    {
        // Reconciliation talks to the same class of server that just failed to
        // answer, so it needs its own deadline. Without one, "find out what
        // happened" inherits exactly the hang it was called to resolve.
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(callerToken);
        deadline.CancelAfter(_operationTimeout);
        var ct = deadline.Token;

        McpConnectorSession session;
        try
        {
            session = await _sessions.ReconnectAsync(intent.ConnectorId, ct);
        }
        catch (Exception ex) when (ex is McpProtocolException or IOException
                                       or SandboxUnavailableException
                                       or InvalidOperationException
                                       or McpAuthorizationException
                                       or HttpRequestException
                                       or System.Net.Sockets.SocketException
                                       || (ex is OperationCanceledException
                                           && !callerToken.IsCancellationRequested))
        {
            // The intent stays OPEN on purpose: an unreachable connector must not
            // clear the record that something may have happened.
            return Result(intent, McpOperationStatus.NeedsUser,
                "ReconciliationUnreachable",
                $"The connector could not be reached to establish what happened "
                + $"({ex.GetType().Name}). The operation stays unresolved until a person "
                + "confirms it.",
                ["reconcile=unreachable", "intent=left_open"]);
        }

        // The fingerprint lookup is what makes a lost answer recoverable: the key
        // went out WITH the mutation, so the server can recognise the act even
        // though we never learned the object's id.
        var byFingerprint = await LookupByFingerprintAsync(session, intent, ct);
        if (byFingerprint is { } found)
        {
            await _journal.ResolveAsync(intent.IntentId, McpOperationStatus.Succeeded,
                "The server holds the object created by this exact dispatch.",
                found, ct);
            return Result(intent, McpOperationStatus.Succeeded,
                null,
                "The mutation was confirmed by looking it up on the server; it was not "
                + "dispatched again.",
                [
                    "reconcile=fingerprint_match",
                    $"object_id={found}",
                    $"generation={session.Generation}",
                    "redispatch=none"
                ],
                reconciledWithoutRedispatch: true);
        }

        McpMutationOutcome outcome;
        try
        {
            outcome = await _reconciler.ReconcileAsync(
                session.Client,
                new McpMutationIntent
                {
                    ConnectorId = intent.ConnectorId,
                    ToolName = intent.ToolName,
                    ArgumentsFingerprint = intent.ArgumentsFingerprint,
                    IdempotencyKey = intent.IdempotencyKey,
                    DispatchedUtc = intent.CreatedUtc,
                    VerificationTool = intent.VerificationTool,
                    ReturnedObjectId = intent.ReturnedObjectId,
                    ExpectedStateValue = intent.ExpectedStateValue
                },
                ct);
        }
        catch (OperationCanceledException) when (!callerToken.IsCancellationRequested)
        {
            // A server that will not answer the question either is still an
            // unresolved mutation, and the intent stays open for the next process.
            return Result(intent, McpOperationStatus.NeedsUser,
                "ReconciliationTimedOut",
                "The connector did not answer the question of what it actually did. "
                + "The operation stays unresolved until a person confirms it.",
                ["reconcile=timed_out", "intent=left_open"]);
        }

        var status = outcome.Verdict switch
        {
            McpMutationVerdict.Succeeded => McpOperationStatus.Succeeded,
            McpMutationVerdict.FailedNoEffect => McpOperationStatus.FailedNoEffect,
            _ => McpOperationStatus.NeedsUser
        };
        if (status == McpOperationStatus.NeedsUser)
            // Left open deliberately: an unresolved mutation is exactly the thing
            // that must still be there after another restart.
            return Result(intent, status, "UnresolvedMutation", outcome.Reason,
                [.. outcome.Evidence, "reconcile=needs_user", "intent=left_open"]);

        await _journal.ResolveAsync(
            intent.IntentId, status, outcome.Reason, outcome.ObjectId, ct);
        return Result(intent, status, null, outcome.Reason,
            [.. outcome.Evidence, "redispatch=none"],
            reconciledWithoutRedispatch: status == McpOperationStatus.Succeeded);
    }

    private async Task<string?> LookupByFingerprintAsync(
        McpConnectorSession session,
        McpDispatchIntent intent,
        CancellationToken ct)
    {
        if (!session.Capabilities.Tools)
            return null;
        try
        {
            var tools = await session.Client.ListToolsAsync(ct);
            if (!tools.Any(tool => string.Equals(
                    tool.Name, FingerprintLookupTool, StringComparison.Ordinal)))
                return null;
            var result = await session.Client.CallToolAsync(
                FingerprintLookupTool,
                JsonSerializer.SerializeToElement(
                    new { idempotency_key = intent.IdempotencyKey }),
                ct);
            if (result.IsError
                || !result.Content.Contains("\"found\":true", StringComparison.OrdinalIgnoreCase))
                return null;
            using var document = JsonDocument.Parse(ExtractJsonObject(result.Content));
            return document.RootElement.TryGetProperty("id", out var id)
                ? id.GetString()
                : null;
        }
        catch (Exception ex) when (ex is McpProtocolException or McpDispatchException
                                       or JsonException or OperationCanceledException)
        {
            return null;
        }
    }

    /// <summary>Convention for a connector that can answer "did you already do
    /// this?". Optional: without it, a lost answer falls back to the object-id
    /// read-back, and failing that, to a person.</summary>
    public const string FingerprintLookupTool = "lookup_by_idempotency_key";

    // ── Dispatch ────────────────────────────────────────────────────────────

    private sealed record DispatchPayload(
        string Content,
        bool IsError,
        string ServerIdentity,
        string? ObjectId);

    /// <summary>
    /// Bounds one dispatch.
    ///
    /// The distinction the whole phase rests on is preserved here: the deadline
    /// expiring means the request WENT OUT and we never learned what happened,
    /// which is an unknown outcome, not a failure. So it is raised as a
    /// dispatched-stage failure and lands in reconciliation — the server is asked
    /// what it actually has, and the mutation is never sent again.
    ///
    /// A cancellation the CALLER asked for is passed through untouched: that is a
    /// different fact and the caller already knows about it.
    /// </summary>
    private async Task<DispatchPayload> DispatchWithinDeadlineAsync(
        McpConnectorSession session,
        McpOperationRequest request,
        McpDispatchIntent intent,
        CancellationToken ct)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(_operationTimeout);
        try
        {
            return await DispatchAsync(session, request, intent, deadline.Token);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            _logger?.LogWarning(
                "Connector {Id} did not answer {Operation} within {Timeout}",
                request.ConnectorId, request.Operation, _operationTimeout);
            throw new McpDispatchException(
                DispatchStage.Dispatched,
                $"The connector accepted the request and did not answer within "
                + $"{_operationTimeout.TotalSeconds:0} seconds.");
        }
    }

    private static async Task<DispatchPayload> DispatchAsync(
        McpConnectorSession session,
        McpOperationRequest request,
        McpDispatchIntent intent,
        CancellationToken ct)
    {
        var identity = session.ServerIdentity;
        switch (request.Operation)
        {
            case McpOperationKind.Initialize:
            case McpOperationKind.Reconnect:
                // Acquiring the session performed the handshake; reporting it is
                // what makes it a step rather than a side channel.
                return new DispatchPayload(
                    JsonSerializer.Serialize(new
                    {
                        server = session.ServerIdentity,
                        protocolVersion = session.Client.NegotiatedProtocolVersion,
                        generation = session.Generation,
                        capabilities = new
                        {
                            tools = session.Capabilities.Tools,
                            resources = session.Capabilities.Resources,
                            prompts = session.Capabilities.Prompts,
                            logging = session.Capabilities.Logging,
                            subscriptions = session.Capabilities.ResourceSubscriptions,
                            completions = session.Capabilities.Completions
                        }
                    }),
                    false, identity, null);

            case McpOperationKind.ToolsList:
            {
                var tools = await session.Client.ListToolsAsync(ct);
                // The schema travels with the listing. Without it the caller that
                // offers these tools to the model would have to dial the server
                // again to learn their shape, and that second dial is exactly the
                // bypass this gateway exists to remove.
                return new DispatchPayload(
                    JsonSerializer.Serialize(tools.Select(tool => new
                    {
                        tool.Name,
                        tool.Description,
                        InputSchema = ParseSchemaOrEmpty(tool.InputSchema)
                    })),
                    false, identity, null);
            }

            case McpOperationKind.ToolsCall:
            {
                using var arguments = JsonDocument.Parse(
                    string.IsNullOrWhiteSpace(request.ArgumentsJson)
                        ? "{}"
                        : request.ArgumentsJson);
                var call = await session.Client.CallToolAsync(
                    request.ToolName!, arguments.RootElement, intent.IdempotencyKey, ct);
                return new DispatchPayload(
                    call.Content, call.IsError, identity, ExtractObjectId(call.Content));
            }

            case McpOperationKind.ResourcesList:
            {
                var resources = await session.Client.ListResourcesAsync(ct);
                return new DispatchPayload(
                    JsonSerializer.Serialize(resources.Select(resource => new
                    {
                        resource.Uri,
                        resource.Name,
                        resource.MimeType
                    })),
                    false, identity, null);
            }

            case McpOperationKind.ResourcesRead:
            {
                var content = await session.Client.ReadResourceAsync(request.ResourceUri!, ct);
                return new DispatchPayload(content.Text, false, identity, null);
            }

            case McpOperationKind.PromptsList:
            {
                var prompts = await session.Client.ListPromptsAsync(ct);
                return new DispatchPayload(
                    JsonSerializer.Serialize(prompts.Select(prompt => new
                    {
                        prompt.Name,
                        prompt.Description
                    })),
                    false, identity, null);
            }

            case McpOperationKind.PromptsGet:
            {
                var prompt = await session.Client.GetPromptAsync(request.PromptName!, null, ct);
                return new DispatchPayload(prompt.Text, false, identity, null);
            }

            case McpOperationKind.Cancel:
                return new DispatchPayload("cancellation requested", false, identity, null);

            case McpOperationKind.Shutdown:
                return new DispatchPayload("shutdown requested", false, identity, null);

            default:
                throw new McpProtocolException(
                    $"CapabilityUnavailable: '{request.Operation}' has no dispatch path.");
        }
    }

    private static string? RequiredCapabilityMissing(
        McpConnectorSession session,
        McpOperationRequest request) => request.Operation switch
    {
        McpOperationKind.ToolsList or McpOperationKind.ToolsCall
            when !session.Capabilities.Tools =>
            "CapabilityUnavailable: the server did not declare 'tools'.",
        McpOperationKind.ResourcesList or McpOperationKind.ResourcesRead
            when !session.Capabilities.Resources =>
            "CapabilityUnavailable: the server did not declare 'resources'.",
        McpOperationKind.PromptsList or McpOperationKind.PromptsGet
            when !session.Capabilities.Prompts =>
            "CapabilityUnavailable: the server did not declare 'prompts'.",
        _ => null
    };

    // ── Plumbing ────────────────────────────────────────────────────────────

    private async Task<string> ResolveActionIdAsync(
        string runId,
        string toolCallId,
        CancellationToken ct)
    {
        try
        {
            var events = await _runtime.GetEventsAsync(runId, ct: ct);
            return events
                       .Where(item => item.ToolCallId == toolCallId
                                      && item.ActionId is { Length: > 0 })
                       .Select(item => item.ActionId!)
                       .LastOrDefault()
                   ?? "";
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException)
        {
            return "";
        }
    }

    private static (McpOperationResult, ToolResult) Terminal(
        string runId,
        string actionId,
        McpOperationRequest request,
        McpOperationStatus status,
        string failureCode,
        string failureReason,
        IReadOnlyList<string> evidence,
        McpRiskClass risk = McpRiskClass.Unknown,
        bool retryable = false,
        string? intentId = null,
        string? content = null)
    {
        var result = new McpOperationResult
        {
            Status = status,
            ConnectorId = request.ConnectorId,
            Operation = request.Operation,
            Content = content ?? "",
            FailureCode = failureCode,
            FailureReason = failureReason,
            RunId = runId,
            ActionId = actionId,
            IntentId = intentId,
            Risk = risk,
            Retryable = retryable,
            Evidence = evidence
        };
        return (result, ToToolResult(result));
    }

    private static McpOperationResult Result(
        McpDispatchIntent intent,
        McpOperationStatus status,
        string? failureCode,
        string reason,
        IReadOnlyList<string> evidence,
        bool reconciledWithoutRedispatch = false) => new()
    {
        Status = status,
        ConnectorId = intent.ConnectorId,
        Operation = intent.Operation,
        FailureCode = failureCode,
        FailureReason = reason,
        RunId = intent.RunId,
        ActionId = intent.ActionId,
        IntentId = intent.IntentId,
        Risk = intent.Risk,
        Phase = McpDispatchPhase.Reconciled,
        Evidence = evidence,
        ReconciledWithoutRedispatch = reconciledWithoutRedispatch
    };

    /// <summary>
    /// Bridges the MCP verdict into the vocabulary the durable runtime records.
    /// NeedsUser and UnknownOutcome deliberately arrive as unverified failures
    /// with retryable=false: the runtime must not be told the step is done, and
    /// must not be invited to try again.
    /// </summary>
    private static ToolResult ToToolResult(McpOperationResult result) => result.Status switch
    {
        McpOperationStatus.Succeeded => ToolResult.Verified(
            result.Content, result.Evidence),
        McpOperationStatus.FailedNoEffect => ToolResult.FailedEffect(
            result.FailureCode ?? "McpFailedNoEffect",
            result.FailureReason ?? "The operation did not take effect.",
            result.Retryable),
        McpOperationStatus.CapabilityUnavailable => ToolResult.FailedEffect(
            "CapabilityUnavailable",
            result.FailureReason ?? "The feature is not available.",
            retryable: false),
        McpOperationStatus.Blocked => ToolResult.FailedEffect(
            result.FailureCode ?? "McpBlocked",
            result.FailureReason ?? "The operation was refused before dispatch.",
            retryable: false),
        _ => ToolResult.FailedEffect(
            result.FailureCode ?? "UnknownOutcome",
            result.FailureReason
            ?? "The operation reached the connector and its effect is unknown.",
            retryable: false)
    };

    private static string DescribeIntent(McpOperationRequest request) => request.Operation switch
    {
        McpOperationKind.ToolsCall =>
            $"Run the {request.ToolName} tool on the {request.ConnectorId} connector",
        McpOperationKind.ResourcesRead =>
            $"Read {request.ResourceUri} from the {request.ConnectorId} connector",
        McpOperationKind.PromptsGet =>
            $"Get the {request.PromptName} prompt from the {request.ConnectorId} connector",
        _ => $"Run {request.Method} on the {request.ConnectorId} connector"
    };

    /// <summary>A tool whose schema will not parse gets the empty object rather
    /// than a permissive one: the schema validator downstream then rejects it,
    /// which is the right outcome for a tool we cannot constrain.</summary>
    private static JsonElement ParseSchemaOrEmpty(string schema)
    {
        try
        {
            return JsonDocument.Parse(schema).RootElement.Clone();
        }
        catch (JsonException)
        {
            return JsonDocument.Parse("{}").RootElement.Clone();
        }
    }

    private static string? ExtractObjectId(string content)
    {
        try
        {
            using var document = JsonDocument.Parse(ExtractJsonObject(content));
            return document.RootElement.TryGetProperty("id", out var id)
                ? id.GetString()
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static string ExtractJsonObject(string content)
    {
        var start = content.IndexOf('{');
        var end = content.LastIndexOf('}');
        return start >= 0 && end > start ? content[start..(end + 1)] : "{}";
    }
}
