using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

/// <summary>
/// THE message loop for every desktop turn (Claude-style always-on agent):
/// stream a completion from the gateway with all desktop tools attached,
/// execute any tool calls locally (with permission checks + consent), append
/// the results, and repeat until the model produces a final answer or a
/// safety cap hits. The model decides on its own when tools are needed.
/// </summary>
public sealed class ChatOrchestrator : IChatOrchestrator
{
    internal const string ComputerActionMarker = "[computer_action_status]";
    internal const string AgentToolStatusMarker = "[agent_tool_status]";
    internal const string ToolEffectMarker = "[tool_effect_status]";
    internal const string UnverifiedActionMessage =
        "The action was executed, but its UI effect has not been verified. Task completion is not claimed.";

    /// <summary>Default safety caps for a top-level turn. These are now a SOFT
    /// budget (Phase A PART 1/6): reaching them pauses the turn with its tool
    /// history intact and offers Continue, rather than abandoning the task — so
    /// the ceiling can be much higher than the old 25 without risking a runaway.
    /// A nested run (run_subagent, Phase 12 F) constructs its own instance with
    /// tighter caps via the optional constructor parameters.</summary>
    public const int DefaultMaxIterations = 100;
    public static readonly TimeSpan DefaultMaxDuration = TimeSpan.FromMinutes(20);

    /// <summary>Bounded number of consecutive "you stopped but the task isn't done"
    /// nudges the loop will feed back to the model before it accepts the turn as
    /// incomplete. This makes the agent recover (try another capability / re-observe /
    /// revise the plan) instead of giving up after one or two failed actions — while
    /// the ceiling (and the reset-on-real-tool-call below) prevents an infinite,
    /// token-burning loop. It never relaxes the "no success until DoD verified" rule.</summary>
    internal const int MaxRecoveryNudges = 3;

    // A durable task can still make progress unless it has reached a terminal state.
    private static bool IsRecoverableStatus(AgentTaskStatus status) =>
        status is not (AgentTaskStatus.Completed or AgentTaskStatus.Failed
            or AgentTaskStatus.Cancelled or AgentTaskStatus.Abandoned);

    private readonly int _maxIterations;
    private readonly TimeSpan _maxDuration;

    /// <summary>Assumed context window when the model catalog has none.</summary>
    private const int FallbackContextWindow = 100_000;

    /// <summary>Output cap sent as max_tokens and reserved by the budgeter.</summary>
    private const int DefaultMaxOutputTokens = 8192;

    private string TimeLimitMessage =>
        $"Stopped: the {_maxDuration.TotalMinutes:0} minute time limit was reached.";

    private readonly IGatewayApiClient _gateway;
    private readonly IMcpToolHost _toolHost;
    private readonly IPermissionService _permissions;
    private readonly IConversationStore _store;
    private readonly ILogger<ChatOrchestrator> _logger;
    private readonly ShellSession? _shellSession;
    private readonly IToolScopeResolver? _scopeResolver;
    private readonly ISettingsService? _settings;
    private readonly IDurableTaskRuntime? _durableRuntime;
    private readonly ISecurityEventLogger? _securityEvents;
    private readonly RuntimeStabilizationOptions _stabilization;

    public event Func<ToolConsentRequest, Task<ToolConsentResponse>>? ConsentRequested;

    /// <inheritdoc />
    public Func<ToolConsentRequest, Task<ToolConsentResponse>>? ConsentHandler => ConsentRequested;

    public ChatOrchestrator(
        IGatewayApiClient gateway,
        IMcpToolHost toolHost,
        IPermissionService permissions,
        IConversationStore store,
        ILogger<ChatOrchestrator> logger,
        int maxIterations = DefaultMaxIterations,
        TimeSpan? maxDuration = null,
        ShellSession? shellSession = null,
        IToolScopeResolver? scopeResolver = null,
        ISettingsService? settings = null,
        IDurableTaskRuntime? durableRuntime = null,
        RuntimeStabilizationOptions? stabilization = null,
        ISecurityEventLogger? securityEvents = null)
    {
        if (maxIterations < 1)
            throw new ArgumentOutOfRangeException(nameof(maxIterations), "At least one iteration is required.");

        _gateway = gateway;
        _toolHost = toolHost;
        _permissions = permissions;
        _store = store;
        _logger = logger;
        _maxIterations = maxIterations;
        _maxDuration = maxDuration ?? DefaultMaxDuration;
        _shellSession = shellSession;
        _scopeResolver = scopeResolver;
        _settings = settings;
        _durableRuntime = durableRuntime;
        _stabilization = stabilization ?? new RuntimeStabilizationOptions();
        _securityEvents = securityEvents;
    }

    /// <summary>
    /// Overlay the user's global approval mode (Cowork-style) on top of the
    /// per-tool decision. Deny always wins; beyond that: Manual forces the
    /// consent dialog for every action, Skip auto-approves everything, and
    /// Auto (default) auto-approves anything ToolDangerClassifier considers
    /// safe while still pausing for dangerous (side-effecting) tools.
    /// No settings wired (tests / nested defaults) → behavior unchanged.
    /// </summary>
    private PermissionDecision ApplyApprovalMode(
        PermissionDecision decision,
        IMcpTool tool,
        string argumentsJson)
    {
        if (decision == PermissionDecision.Denied)
            return decision;

        var sideEffect = AgentSafetyPolicy.Classify(tool.Name, argumentsJson, tool.Category);
        if (AgentSafetyPolicy.RequiresInvariantApproval(sideEffect))
            return PermissionDecision.NeedsConsent;
        if (_settings is null)
            return decision;

        var dangerous = ToolDangerClassifier.Classify(tool.Name, tool.Category).IsDangerous;
        return ApprovalModeParser.Parse(_settings.Current.ApprovalMode) switch
        {
            // Manual: pause for every side-effecting action. Read-only/meta
            // tools that were already auto-approvable stay auto — this also
            // keeps run_subagent fan-out (which has no consent handler and a
            // safe-tools-only surface) from denying everything.
            ApprovalMode.Manual => dangerous || decision == PermissionDecision.NeedsConsent
                ? PermissionDecision.NeedsConsent
                : decision,
            ApprovalMode.Skip => PermissionDecision.Auto,
            _ => decision == PermissionDecision.NeedsConsent && !dangerous
                ? PermissionDecision.Auto
                : decision
        };
    }

    public async IAsyncEnumerable<AgentStreamEvent> RunAsync(
        string model,
        List<GatewayMessage> messages,
        string conversationId,
        int? contextWindow = null,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var tools = _toolHost.GetToolDefinitions();
        var deadline = DateTimeOffset.UtcNow + _maxDuration;
        var window = contextWindow ?? FallbackContextWindow;
        var contextRetryUsed = false;
        var iteration = 0;
        // Consecutive no-tool-call recovery nudges; reset to 0 whenever the model
        // actually calls a tool again (real progress). Capped by MaxRecoveryNudges.
        var recoveryNudges = 0;
        DurableTurnContext? durableContext = null;
        AgentErrorEvent? durableInitializationError = null;
        if (_durableRuntime is not null)
        {
            try
            {
                durableContext = await _durableRuntime.StartOrResumeAsync(
                    conversationId, messages, $"conversation:{conversationId}", ct);
                if (durableContext is not null)
                    UpsertPinnedContext(messages, durableContext.PinnedContext);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Durable task initialization failed");
                durableInitializationError = new AgentErrorEvent(
                    "The durable task state could not be initialized; no action was executed.",
                    "durable_task_initialization_failed");
            }
        }
        if (durableInitializationError is not null)
        {
            yield return durableInitializationError;
            yield break;
        }
        if (durableContext is not null && _stabilization.AuthoritativePlanUI)
        {
            yield return new AgentAuthoritativePlanEvent(
                await _durableRuntime!.GetAuthoritativePlanAsync(durableContext.TaskId, ct));
            yield return new AgentDurableEventsEvent(
                durableContext.TaskId,
                await _durableRuntime.GetEventsAsync(durableContext.TaskId, ct: ct));
        }

        // The durable runtime is wired but declined this turn (the request wasn't
        // an actionable goal). Don't silently drop the verification guarantees —
        // surface a one-time, non-blocking notice that we're in conversation mode.
        if (_durableRuntime is not null && durableContext is null)
            yield return new AgentVerificationModeEvent(
                "Running in conversation mode — this turn's result is not independently verified.");

        if (durableContext?.Mode == AgentTaskMode.Conversation)
            yield return new AgentVerificationModeEvent(
                "This conversation turn is durably recorded; no tool side effect is being claimed.");

        while (iteration < _maxIterations && DateTimeOffset.UtcNow < deadline)
        {
            ct.ThrowIfCancellationRequested();
            iteration++;
            yield return new AgentStatusEvent(
                iteration == 1 ? "thinking" : $"continuing (step {iteration})",
                iteration, _maxIterations);

            // Keep the request inside the model's context window: old
            // screenshots become placeholders, then whole history groups are
            // trimmed oldest-first if the estimate still exceeds the budget.
            // How many recent screenshots to keep scales with the window
            // (Phase 14 B) — bigger windows can afford more visual context.
            ScreenshotPruner.Prune(messages, ScreenshotPruner.KeepLatestFor(window));
            var budget = ContextBudgeter.FitToBudget(messages, window, DefaultMaxOutputTokens);
            if (budget.Trimmed)
                yield return new AgentContextTrimmedEvent(budget.OmittedMessages);

            var request = new GatewayCompletionRequest
            {
                Model = model,
                Messages = messages,
                Tools = tools.Count > 0 ? tools.ToList() : null,
                ToolChoice = tools.Count > 0 ? "auto" : null,
                MaxTokens = DefaultMaxOutputTokens
            };

            var parser = new GatewayChunkParser();
            var pendingToolCalls = new List<GatewayToolCall>();
            var assistantContent = new StringBuilder();
            AgentErrorEvent? errorEvent = null;
            var pendingUnverifiedAction = HasPendingUnverifiedAction(messages);
            // True once any content delta has been streamed live this iteration —
            // the durable completion path uses it to append only the verification
            // suffix instead of re-emitting the (already shown) body.
            var streamedAssistantContent = false;

            await foreach (var frame in _gateway.StreamCompletionAsync(request, ct))
            {
                foreach (var evt in parser.Parse(frame.Data))
                {
                    switch (evt)
                    {
                        case AgentContentDeltaEvent delta:
                            assistantContent.Append(delta.Text);
                            // Stream tokens live even under the durable runtime
                            // (Claude/ChatGPT-style) — success is still not claimed
                            // until EvaluateCompletionAsync passes and the
                            // verification suffix is appended below. Suppressed only
                            // while a prior action is still unverified, so a model's
                            // premature "done" can't leak ahead of verification.
                            if (!pendingUnverifiedAction)
                            {
                                streamedAssistantContent = true;
                                yield return delta;
                            }
                            break;

                        case AgentToolCallsEvent toolCallsEvt:
                            pendingToolCalls.AddRange(toolCallsEvt.ToolCalls);
                            break;

                        case AgentStreamDoneEvent:
                            // Finish signal only — whether this turn is final is
                            // decided below by pendingToolCalls.
                            break;

                        case AgentErrorEvent err:
                            errorEvent = err;
                            break;
                    }

                    if (errorEvent is not null) break;
                }

                if (errorEvent is not null) break;
            }

            if (errorEvent is not null)
            {
                if (errorEvent.Code == "context_length_exceeded" && !contextRetryUsed)
                {
                    // The estimator undercounted for this provider — recover
                    // aggressively and replay this iteration once.
                    contextRetryUsed = true;
                    _logger.LogWarning(
                        "Context window exceeded for {Model}; trimming and retrying", model);
                    ScreenshotPruner.Prune(messages, keepLatest: 1);
                    var recovery = ContextBudgeter.FitToBudget(
                        messages, window / 2, DefaultMaxOutputTokens);
                    yield return new AgentContextTrimmedEvent(recovery.OmittedMessages);
                    iteration--; // the retry must not consume a step
                    continue;
                }

                if (errorEvent.Code == "context_length_exceeded")
                {
                    if (durableContext is not null)
                        await _durableRuntime!.PauseAsync(
                            durableContext, "Context window exceeded after retry.", ct);
                    yield return errorEvent with
                    {
                        Message = errorEvent.Message +
                                  " This conversation is too long for the model — start a new chat to continue."
                    };
                    yield break;
                }

                if (durableContext is not null)
                    await _durableRuntime!.PauseAsync(
                        durableContext, $"Provider failure: {errorEvent.Code ?? "unknown"}", ct);
                yield return errorEvent;
                yield break;
            }

            // The stream can end without any finish_reason (connection closed;
            // bare [DONE] is drained by the parser itself) — recover fully
            // accumulated tool calls instead of treating the turn as final.
            foreach (var call in parser.DrainRemaining())
            {
                if (pendingToolCalls.All(c => c.Id != call.Id))
                    pendingToolCalls.Add(call);
            }

            if (pendingToolCalls.Count == 0)
            {
                if (durableContext?.Mode == AgentTaskMode.Conversation)
                {
                    await _durableRuntime!.CompleteConversationAsync(
                        durableContext,
                        $"sha256:{SensitiveDataRedactor.Fingerprint(assistantContent.ToString())}",
                        ct);
                    yield return new AgentDurableEventsEvent(
                        durableContext.TaskId,
                        await _durableRuntime.GetEventsAsync(durableContext.TaskId, ct: ct));
                    yield return new AgentStreamDoneEvent();
                    yield break;
                }
                if (durableContext is not null)
                {
                    var completion = await _durableRuntime!.EvaluateCompletionAsync(
                        durableContext, ct);
                    if (_stabilization.AuthoritativePlanUI)
                        yield return new AgentAuthoritativePlanEvent(
                            await _durableRuntime.GetAuthoritativePlanAsync(durableContext.TaskId, ct));

                    // The model stopped calling tools but the task is NOT complete and
                    // is still recoverable — don't abandon it. Feed a bounded nudge back
                    // in so the model tries a different capability, re-observes, or
                    // revises the plan (Claude-desktop-style persistence). The cap +
                    // reset-on-real-tool-call keep this from looping forever, and
                    // completion stays authoritative: success is never claimed here.
                    if (!completion.Completed
                        && IsRecoverableStatus(completion.Status)
                        && recoveryNudges < MaxRecoveryNudges)
                    {
                        recoveryNudges++;
                        yield return new AgentVerificationModeEvent(
                            $"Recovering — the task isn't complete yet. Trying another approach " +
                            $"(attempt {recoveryNudges}/{MaxRecoveryNudges}).");
                        messages.Add(new GatewayMessage
                        {
                            Role = "system",
                            Content =
                                $"The task is NOT complete: {completion.Message} Do not stop here. " +
                                "Try a DIFFERENT capability or approach, refresh state with an " +
                                "observation (e.g. read_screen_elements) first, or call update_plan to " +
                                "revise the plan. Continue until the Definition of Done is verified or " +
                                "all safe options are exhausted."
                        });
                        continue;
                    }

                    // Verification is authoritative regardless of what the model
                    // streamed. If the body was already streamed live, append ONLY
                    // the verification/status suffix so it isn't duplicated;
                    // otherwise emit the full composed response.
                    string finalText;
                    if (completion.Completed)
                        finalText = streamedAssistantContent
                            ? VerificationSuffix(completion)
                            : BuildVerifiedFinalResponse(assistantContent.ToString(), completion);
                    else
                    {
                        var status = $"{completion.Message} TaskId={durableContext.TaskId}.";
                        finalText = streamedAssistantContent ? $"\n\n{status}" : status;
                    }
                    yield return new AgentContentDeltaEvent(finalText);
                }
                else if (pendingUnverifiedAction)
                    yield return new AgentContentDeltaEvent(UnverifiedActionMessage);
                yield return new AgentStreamDoneEvent();
                yield break;
            }

            yield return new AgentToolCallsEvent(pendingToolCalls);
            // Real progress: the model is acting again, so clear the recovery-nudge
            // streak — the ceiling only guards CONSECUTIVE no-tool-call stalls.
            recoveryNudges = 0;

            // Assistant message WITH tool_calls goes into history first —
            // providers reject tool results that don't follow their request.
            messages.Add(new GatewayMessage
            {
                Role = "assistant",
                Content = assistantContent.Length > 0 ? assistantContent.ToString() : null,
                ToolCalls = pendingToolCalls.ToList()
            });

            var pausedAtDeadline = false;
            foreach (var toolCall in pendingToolCalls)
            {
                ct.ThrowIfCancellationRequested();

                // The while-condition only checks the deadline between
                // iterations — don't start another tool past it. Rather than
                // abandon the turn, answer the remaining tool calls with a
                // "paused" placeholder so the chain stays valid (every tool_call
                // needs a result) and the turn can be RESUMED (PART 1/6).
                if (pausedAtDeadline || DateTimeOffset.UtcNow >= deadline)
                {
                    pausedAtDeadline = true;
                    messages.Add(ToolResultMessage(toolCall.Id, toolCall.Function.Name,
                        "Not executed: the turn was paused at its time limit. It can be resumed to continue."));
                    continue;
                }

                await foreach (var evt in ExecuteToolCallAsync(
                                   toolCall, messages, conversationId, durableContext, ct))
                    yield return evt;

                if (durableContext is not null)
                {
                    durableContext = await _durableRuntime!.StartOrResumeAsync(
                        conversationId,
                        Array.Empty<GatewayMessage>(),
                        $"conversation:{conversationId}",
                        ct);
                    if (durableContext is not null)
                    {
                        UpsertPinnedContext(messages, durableContext.PinnedContext);
                        if (_stabilization.AuthoritativePlanUI)
                        {
                            yield return new AgentAuthoritativePlanEvent(
                                await _durableRuntime.GetAuthoritativePlanAsync(
                                    durableContext.TaskId, ct));
                            yield return new AgentDurableEventsEvent(
                                durableContext.TaskId,
                                await _durableRuntime.GetEventsAsync(
                                    durableContext.TaskId,
                                    ct: ct));
                        }
                    }
                }
            }

            if (pausedAtDeadline)
            {
                if (durableContext is not null)
                    await _durableRuntime!.PauseAsync(durableContext, TimeLimitMessage, ct);
                yield return new AgentPausedEvent(TimeLimitMessage);
                yield break;
            }
        }

        // The cap is a checkpoint, not a cliff: the accumulated tool history is
        // intact in `messages` (the caller's list), so the UI can offer Continue
        // and re-enter this loop to resume the task (PART 1/6).
        var pauseReason = iteration >= _maxIterations
            ? $"Paused: reached the {_maxIterations}-step limit. The task can be continued."
            : TimeLimitMessage;
        if (durableContext is not null)
            await _durableRuntime!.PauseAsync(durableContext, pauseReason, ct);
        yield return new AgentPausedEvent(pauseReason);
    }

    private async IAsyncEnumerable<AgentStreamEvent> ExecuteToolCallAsync(
        GatewayToolCall toolCall,
        List<GatewayMessage> messages,
        string conversationId,
        DurableTurnContext? durableContext,
        [EnumeratorCancellation] CancellationToken ct)
    {
        var name = toolCall.Function.Name;
        var argumentsJson = toolCall.Function.Arguments;

        // Gateway DLP can replace arguments with a blocked marker — never
        // execute those (the values the model intended are gone).
        if (IsDlpBlocked(argumentsJson, out var dlpReason))
        {
            const string dlpMessage = "Error: the gateway's DLP policy blocked this tool call's arguments " +
                                      "(sensitive content was masked). Explain this to the user and try a " +
                                      "different approach if possible.";
            messages.Add(ToolResultMessage(toolCall.Id, name, dlpMessage));
            yield return new AgentToolResultEvent(toolCall.Id, name, argumentsJson, false,
                $"Blocked by DLP ({dlpReason})", 0);
            await LogExecutionAsync(conversationId, toolCall, "denied", null, $"dlp:{dlpReason}", null, ct);
            yield break;
        }

        var tool = _toolHost.Resolve(name);
        if (tool is null)
        {
            messages.Add(ToolResultMessage(toolCall.Id, name, $"Error: unknown tool '{name}'."));
            yield return new AgentToolResultEvent(toolCall.Id, name, argumentsJson, false, "Unknown tool", 0);
            yield break;
        }

        string? validationFailure = null;
        try
        {
            using var validationDocument = JsonDocument.Parse(
                string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
            var validation = tool.ValidateRequest(validationDocument.RootElement);
            if (!validation.Valid)
                validationFailure =
                    $"Error: tool request validation failed ({validation.FailureCode}): {validation.Reason}";
        }
        catch (JsonException ex)
        {
            validationFailure = $"Error: invalid tool arguments JSON: {ex.Message}";
        }

        if (validationFailure is not null)
        {
            messages.Add(ToolResultMessage(toolCall.Id, name, validationFailure));
            yield return new AgentToolResultEvent(
                toolCall.Id,
                name,
                argumentsJson,
                false,
                validationFailure,
                0,
                FullResult: validationFailure);
            yield break;
        }

        // Folder-scoped consent (PART 4): the resolver maps writes to their
        // project folder and shell to its cwd; falls back to the exact-path
        // scope when no resolver is wired (tests).
        var scope = _scopeResolver?.Resolve(tool, argumentsJson, conversationId)
                    ?? ExtractScope(tool, argumentsJson);
        var sideEffect = AgentSafetyPolicy.Classify(tool.Name, argumentsJson, tool.Category);
        var invariantApproval = AgentSafetyPolicy.RequiresInvariantApproval(sideEffect);
        var dataFlow = (tool as ISensitiveDataTool)?.DataFlowPolicy;
        var separateCaptureConsent = RequiresSeparateCaptureTransmissionConsent(dataFlow);
        var egress = DataEgressPolicy.Evaluate(
            tool, argumentsJson, sideEffect, dataFlow, messages);
        if (!egress.Allowed)
        {
            var blocked = $"Error: security policy blocked this tool call " +
                          $"({egress.ReasonCode}).";
            messages.Add(ToolResultMessage(toolCall.Id, tool.Name, blocked));
            yield return new AgentToolResultEvent(
                toolCall.Id, tool.Name, "{}", false, blocked, 0);
            _securityEvents?.Write(new SecurityAuditEvent(
                "data_egress.authorization",
                "denied",
                tool.Name,
                dataFlow?.ExternalAction,
                egress.Sensitivity.ToString(),
                tool.Category.ToString(),
                egress.Destination,
                egress.ExternalTransmission,
                false,
                egress.ReasonCode,
                CorrelationId: toolCall.Id));
            await LogExecutionAsync(
                conversationId, toolCall, "denied", null, egress.ReasonCode, null, ct);
            yield break;
        }
        var decision = ApplyApprovalMode(
            await _permissions.CheckAsync(tool, scope, ct), tool, argumentsJson);
        if ((dataFlow?.AlwaysRequireConfirmation == true || egress.RequiresConsent)
            && decision != PermissionDecision.Denied)
            decision = PermissionDecision.NeedsConsent;
        _securityEvents?.Write(new SecurityAuditEvent(
            "tool.authorization",
            decision.ToString(),
            tool.Name,
            dataFlow?.LocalAction,
            sideEffect.ToString(),
            tool.Category.ToString(),
            egress.Destination,
            egress.ExternalTransmission,
            CorrelationId: toolCall.Id));

        if (decision == PermissionDecision.NeedsConsent)
        {
            yield return new AgentToolConsentRequestEvent(
                toolCall.Id, tool.Name, SummarizeArgs(argumentsJson), tool.DefaultPermission);

            var response = ConsentRequested is not null
                ? await ConsentRequested(new ToolConsentRequest(
                    toolCall.Id,
                    tool.Name,
                    tool.Description,
                    tool.Category,
                    argumentsJson,
                    scope,
                    Action: dataFlow is null
                        ? egress.ReasonCode
                        : separateCaptureConsent
                            ? dataFlow.LocalAction
                        : dataFlow.ExternalAction is null
                            ? dataFlow.LocalAction
                            : $"{dataFlow.LocalAction} then {dataFlow.ExternalAction}",
                    Destination: separateCaptureConsent
                        ? "local device"
                        : egress.Destination,
                    Sensitivity: egress.Sensitivity.ToString().ToLowerInvariant(),
                    ExternalTransmission:
                        !separateCaptureConsent && egress.ExternalTransmission,
                    AlwaysRequireConfirmation:
                        dataFlow?.AlwaysRequireConfirmation == true || egress.RequiresConsent))
                : ToolConsentResponse.Deny; // no UI handler → safe default

            if (durableContext is not null)
            {
                await _durableRuntime!.RecordApprovalAsync(
                    durableContext,
                    toolCall.Id,
                    tool.Name,
                    argumentsJson,
                    scope,
                    granted: !response.Denied,
                    expiresAtUtc: DateTimeOffset.UtcNow + _stabilization.ApprovalExpiry,
                    ct);
            }

            if (response.Denied)
            {
                RecordConsent(toolCall, tool, dataFlow, "denied");
                messages.Add(ToolResultMessage(toolCall.Id, tool.Name,
                    "Error: the user denied permission for this tool call."));
                yield return new AgentToolResultEvent(toolCall.Id, tool.Name, argumentsJson, false,
                    "Permission denied by user", 0);
                await LogExecutionAsync(conversationId, toolCall, "denied", null, null, null, ct);
                yield break;
            }

            RecordConsent(
                toolCall,
                tool,
                dataFlow,
                response.AllowAlways ? "allow_always" : "allow_once");
            if (response.AllowAlways && !invariantApproval
                                     && dataFlow?.AlwaysRequireConfirmation != true
                                     && !egress.RequiresConsent)
                await _permissions.PersistDecisionAsync(tool.Name, scope, "allow_always", ct);
            else if (response.AllowOnce && !invariantApproval
                                        && dataFlow?.AlwaysRequireConfirmation != true
                                        && !egress.RequiresConsent)
                _permissions.RecordSessionDecision(tool.Name, scope, PermissionLevel.AllowOnce);
        }
        else if (decision == PermissionDecision.Denied)
        {
            messages.Add(ToolResultMessage(toolCall.Id, tool.Name,
                "Error: this tool is blocked by user policy."));
            yield return new AgentToolResultEvent(toolCall.Id, tool.Name, argumentsJson, false,
                "Blocked by policy", 0);
            await LogExecutionAsync(conversationId, toolCall, "denied", null, null, null, ct);
            yield break;
        }

        DurableActionPreparation? durablePreparation = null;
        if (durableContext is not null)
        {
            if (string.Equals(tool.Name, "update_plan", StringComparison.Ordinal))
            {
                durableContext = await _durableRuntime!.ApplyPlanUpdateAsync(
                    durableContext, argumentsJson, ct);
                UpsertPinnedContext(messages, durableContext.PinnedContext);
            }

            durablePreparation = await _durableRuntime!.PrepareActionAsync(
                durableContext, toolCall.Id, tool.Name, tool.Category, argumentsJson, ct);
            if (!durablePreparation.Proceed)
            {
                var failure = $"Error: durable runtime rejected the action " +
                              $"({durablePreparation.FailureCode}): {durablePreparation.FailureReason}";
                messages.Add(ToolResultMessage(toolCall.Id, tool.Name, failure));
                yield return new AgentToolResultEvent(
                    toolCall.Id, tool.Name, argumentsJson, false, failure, 0,
                    FullResult: failure);
                await LogExecutionAsync(
                    conversationId, toolCall, "denied", null,
                    durablePreparation.FailureCode, null, ct);
                yield break;
            }
        }

        var stopwatch = Stopwatch.StartNew();
        ToolResult result;
        StructuredToolObservation? structuredObservation = null;
        ToolContractVerification? contractVerification = null;
        InteractionObservationSnapshot? preActionObservation = null;
        InteractionObservationSnapshot? postActionObservation = null;
        var cancelledUnknownOutcome = false;
        using var runScope = AgentRunExecutionContext.Push(
            durableContext?.TaskId,
            toolCall.Id);
        try
        {
            // Carry the active conversation to the tool via the ambient shell
            // session (flows through AsyncLocal) so per-conversation shell state
            // — the retained working directory — resolves correctly (PART 3).
            if (_shellSession is not null)
                _shellSession.CurrentConversationId = conversationId;

            using var args = JsonDocument.Parse(
                string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
            var prepared = await tool.PrepareAsync(args.RootElement, ct);
            if (tool is IPerceptualActionTool perceptual)
            {
                preActionObservation = await perceptual.ObserveBeforeActionAsync(
                    args.RootElement, ct);
                prepared = prepared with
                {
                    Action = perceptual.DescribeAction(args.RootElement),
                    PreActionObservation = preActionObservation
                };
            }

            if (preActionObservation is { PreconditionsSatisfied: false })
            {
                result = ToolResult.FailedEffect(
                    "ActionPreconditionFailed",
                    string.Join("; ", preActionObservation.Preconditions),
                    retryable: false);
            }
            else
            {
                result = await tool.ExecuteAsync(args.RootElement, ct);
            }

            structuredObservation = await tool.ObserveAsync(result, ct);
            if (tool is IPerceptualActionTool actionTool
                && preActionObservation is not null)
            {
                postActionObservation = await actionTool.ObserveAfterActionAsync(
                    args.RootElement, result, preActionObservation, ct);
                structuredObservation = structuredObservation with
                {
                    PostActionObservation = postActionObservation
                };
                contractVerification = actionTool.VerifyAction(
                    args.RootElement,
                    result,
                    preActionObservation,
                    postActionObservation);
                result = ApplyPerceptualVerification(
                    result,
                    contractVerification,
                    preActionObservation,
                    postActionObservation);
            }
            else
            {
                contractVerification = await tool.VerifyAsync(
                    result,
                    structuredObservation,
                    ct);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            if (durableContext is not null && durablePreparation is not null)
            {
                durableContext = await _durableRuntime!.RecordUnknownOutcomeAsync(
                    durableContext,
                    durablePreparation,
                    "Execution was cancelled after dispatch; the actual side effect is unknown.",
                    CancellationToken.None);
                result = ToolResult.FailedEffect(
                    "UnknownOutcome",
                    "Execution was cancelled after dispatch. The task was checkpointed and " +
                    "must reconcile actual state before retrying.",
                    retryable: false);
                cancelledUnknownOutcome = true;
            }
            else
            {
                throw;
            }
        }
        catch (JsonException ex)
        {
            result = ToolResult.Fail($"Invalid tool arguments JSON: {ex.Message}");
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Tool {Tool} execution failed", tool.Name);
            result = ToolResult.Fail(ex.Message);
        }
        stopwatch.Stop();

        // Clipboard/screen capture and their external transmission are always
        // separate decisions. Other external data flows get a second decision
        // when local DLP recognizes a secret after execution.
        if (result.Success
            && dataFlow?.ExternalTransmission == true
            && (separateCaptureConsent
                || (result.ImageDataUri is null
                    && SensitiveDataRedactor.Classify(result.Content)
                        == Domain.Entities.SensitiveDataClassification.Secret)))
        {
            var detectedSensitivity = result.ImageDataUri is not null
                ? dataFlow.Sensitivity
                : SensitiveDataRedactor.Classify(result.Content)
                    .ToString()
                    .ToLowerInvariant();
            var transmission = ConsentRequested is not null
                ? await ConsentRequested(new ToolConsentRequest(
                    toolCall.Id,
                    tool.Name,
                    "Approve sending the locally captured data to the configured destination.",
                    tool.Category,
                    "{}",
                    scope,
                    Action: dataFlow.ExternalAction,
                    Destination: dataFlow.Destination,
                    Sensitivity: detectedSensitivity,
                    ExternalTransmission: true,
                    AlwaysRequireConfirmation: true))
                : ToolConsentResponse.Deny;
            if (transmission.Denied)
                result = ToolResult.Fail(
                    "Local data was captured but external transmission was denied. " +
                    "The payload was discarded.");
            _securityEvents?.Write(new SecurityAuditEvent(
                "data_egress.authorization",
                transmission.Denied ? "denied" : "allow_once",
                tool.Name,
                dataFlow.ExternalAction,
                detectedSensitivity,
                dataFlow.SourceType,
                dataFlow.Destination,
                true,
                !transmission.Denied,
                transmission.Denied ? "SensitiveTransmissionDenied" : null,
                CorrelationId: toolCall.Id));
        }

        AgentToolResult? unifiedResult = null;
        if (durableContext is not null && durablePreparation is not null
            && !cancelledUnknownOutcome)
        {
            try
            {
                var outcome = await _durableRuntime!.RecordResultAsync(
                    durableContext, durablePreparation, result, stopwatch.ElapsedMilliseconds, ct);
                unifiedResult = outcome.Result;
                durableContext = outcome.Context;
                UpsertPinnedContext(messages, durableContext.PinnedContext);
            }
            catch (Exception ex)
            {
                // The pre-action checkpoint remains reconciliation-required.
                // Never repeat the action or report success when the durable
                // outcome transaction did not commit.
                _logger.LogError(ex, "Durable result commit failed for {ToolCallId}", toolCall.Id);
                result = ToolResult.FailedEffect(
                    "DurableStateCommitFailed",
                    "The action may have executed, but its durable result could not be committed. " +
                    "Reconcile current state before retrying.",
                    retryable: false);
            }
        }

        var verifiedForPresentation = IsVerifiedForPresentation(result);
        var content = verifiedForPresentation
            ? result.TruncatedContent
            : $"Error: {result.Effect?.FailureReason ?? result.ComputerAction?.FailureReason
                ?? result.Error ?? "The action completed without verification evidence."}" +
              (string.IsNullOrWhiteSpace(result.Content)
                  ? string.Empty
                  : $"\nExecution detail: {result.TruncatedContent}");
        if (result.ComputerAction is { } computerAction)
            content += "\n" + ComputerActionMarker + JsonSerializer.Serialize(computerAction);
        if (result.Effect is { } effect)
            content += "\n" + ToolEffectMarker + JsonSerializer.Serialize(effect);
        if (unifiedResult is not null)
            content += "\n" + AgentToolStatusMarker + JsonSerializer.Serialize(unifiedResult);
        messages.Add(ToolResultMessage(toolCall.Id, tool.Name, content));

        // Screenshots ride back as a vision message (see ScreenshotMessage —
        // shared with ScreenshotPruner so old ones can be pruned safely).
        if (result.Success && result.ImageDataUri is { Length: > 0 } dataUri)
            messages.Add(ScreenshotMessage.Create(tool.Name, dataUri));

        yield return new AgentToolResultEvent(
            toolCall.Id, tool.Name, argumentsJson, verifiedForPresentation,
            content.Length > 200 ? content[..200] + "..." : content,
            stopwatch.ElapsedMilliseconds,
            FullResult: content,
            ImageDataUri: result.ImageDataUri,
            ComputerAction: result.ComputerAction,
            UnifiedResult: unifiedResult);

        var wasAutoApproved = decision == PermissionDecision.Auto;
        await LogExecutionAsync(conversationId, toolCall,
            wasAutoApproved ? "auto" : "allowed",
            verifiedForPresentation ? content : null,
            verifiedForPresentation
                ? null
                : result.Effect?.FailureReason ?? result.ComputerAction?.FailureReason
                  ?? result.Error,
            stopwatch.ElapsedMilliseconds,
            cancelledUnknownOutcome ? CancellationToken.None : ct);
        _securityEvents?.Write(new SecurityAuditEvent(
            "tool.execution",
            wasAutoApproved ? "auto" : "allowed",
            tool.Name,
            dataFlow?.LocalAction,
            sideEffect.ToString(),
            tool.Category.ToString(),
            dataFlow?.Destination,
            dataFlow?.ExternalTransmission,
            verifiedForPresentation,
            result.Effect?.FailureCode
            ?? (contractVerification?.Disposition == VerificationDisposition.Failed
                ? "ContractVerificationFailed"
                : verifiedForPresentation ? null : "ToolFailure"),
            stopwatch.ElapsedMilliseconds,
            structuredObservation?.ByteCount ?? result.Content.Length,
            toolCall.Id));
    }

    private void RecordConsent(
        GatewayToolCall call,
        IMcpTool tool,
        ToolDataFlowPolicy? flow,
        string decision) =>
        _securityEvents?.Write(new SecurityAuditEvent(
            "consent.decision",
            decision,
            tool.Name,
            flow?.LocalAction,
            TargetCategory: tool.Category.ToString(),
            DestinationCategory: flow?.Destination,
            ExternalTransmission: flow?.ExternalTransmission,
            Success: decision != "denied",
            CorrelationId: call.Id));

    private static bool RequiresSeparateCaptureTransmissionConsent(
        ToolDataFlowPolicy? flow) =>
        flow?.ExternalTransmission == true
        && flow.LocalAction is "Clipboard.ReadLocal" or "Screen.CaptureLocal";

    internal static bool HasPendingUnverifiedAction(IEnumerable<GatewayMessage> messages)
    {
        foreach (var message in messages.Reverse())
        {
            if (!string.Equals(message.Role, "tool", StringComparison.Ordinal)
                || message.Content is not string content)
                continue;

            var unifiedJson = MarkerPayload(content, AgentToolStatusMarker);
            if (unifiedJson is not null)
            {
                try
                {
                    return JsonSerializer.Deserialize<AgentToolResult>(unifiedJson)
                               ?.StepCompleted != true;
                }
                catch (JsonException)
                {
                    return true;
                }
            }

            var effectJson = MarkerPayload(content, ToolEffectMarker);
            if (effectJson is not null)
            {
                try
                {
                    return JsonSerializer.Deserialize<ToolEffectResult>(effectJson)
                               ?.Verified != true;
                }
                catch (JsonException)
                {
                    return true;
                }
            }

            var json = MarkerPayload(content, ComputerActionMarker);
            if (json is null)
                continue;
            try
            {
                return JsonSerializer.Deserialize<ComputerActionResult>(json)?.TaskCompleted != true;
            }
            catch (JsonException)
            {
                return true;
            }
        }

        return false;
    }

    private static string? MarkerPayload(string content, string marker)
    {
        var markerIndex = content.IndexOf(marker, StringComparison.Ordinal);
        if (markerIndex < 0) return null;
        var start = markerIndex + marker.Length;
        var end = content.IndexOf('\n', start);
        return (end < 0 ? content[start..] : content[start..end]).Trim();
    }

    internal static bool IsDlpBlocked(string argumentsJson, out string reason)
    {
        reason = "";
        try
        {
            using var doc = JsonDocument.Parse(argumentsJson);
            if (doc.RootElement.ValueKind == JsonValueKind.Object
                && doc.RootElement.TryGetProperty("_dlp_blocked", out var blocked)
                && blocked.ValueKind == JsonValueKind.True)
            {
                reason = doc.RootElement.TryGetProperty("reason", out var r)
                    ? r.GetString() ?? "unspecified"
                    : "unspecified";
                return true;
            }
        }
        catch (JsonException)
        {
        }
        return false;
    }

    private static GatewayMessage ToolResultMessage(string toolCallId, string name, string content) =>
        new() { Role = "tool", ToolCallId = toolCallId, Name = name, Content = content };

    private static ToolResult ApplyPerceptualVerification(
        ToolResult result,
        ToolContractVerification verification,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after)
    {
        var observationEvidence = new[]
        {
            $"pre_observation_id={before.ObservationId}",
            $"pre_capture_version={before.CaptureVersion}",
            $"post_observation_id={after.ObservationId}",
            $"post_capture_version={after.CaptureVersion}",
            $"observation_source={after.Source}"
        };
        if (verification.Disposition == VerificationDisposition.Verified)
        {
            if (result.ComputerAction is { } computer)
                return result with
                {
                    ComputerAction = computer with
                    {
                        EffectVerified = true,
                        TaskCompleted = true,
                        FailureCode = null,
                        FailureReason = null,
                        Retryable = false,
                        CaptureBeforeId = before.ObservationId,
                        CaptureAfterId = after.ObservationId,
                        Evidence = computer.Evidence
                            .Concat(verification.Evidence)
                            .Concat(observationEvidence)
                            .Distinct(StringComparer.Ordinal)
                            .ToArray()
                    }
                };
            if (result.Effect is { } effect)
                return result with
                {
                    Effect = effect with
                    {
                        ExpectedEffectObserved = true,
                        Verified = true,
                        FailureCode = null,
                        FailureReason = null,
                        Retryable = false,
                        Evidence = effect.Evidence
                            .Concat(verification.Evidence)
                            .Concat(observationEvidence)
                            .Distinct(StringComparer.Ordinal)
                            .ToArray()
                    }
                };
            return ToolResult.Verified(
                result.Content,
                verification.Evidence.Concat(observationEvidence).ToArray());
        }

        var failureCode = verification.Disposition switch
        {
            VerificationDisposition.NeedsUser => "UnknownOutcome",
            VerificationDisposition.Failed => "PostconditionFailed",
            _ => "PostconditionInconclusive"
        };
        var evidence = (result.Effect?.Evidence
                        ?? result.ComputerAction?.Evidence
                        ?? Array.Empty<string>())
            .Concat(verification.Evidence)
            .Concat(observationEvidence)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        var computerAction = result.ComputerAction is null
            ? null
            : result.ComputerAction with
            {
                EffectVerified = false,
                TaskCompleted = false,
                FailureCode = failureCode,
                FailureReason = verification.Reason,
                Retryable = verification.Disposition == VerificationDisposition.Inconclusive,
                CaptureBeforeId = before.ObservationId,
                CaptureAfterId = after.ObservationId,
                Evidence = result.ComputerAction.Evidence.Concat(evidence)
                    .Distinct(StringComparer.Ordinal)
                    .ToArray()
            };
        var nativeSuccess = result.Effect?.NativeSuccess
                            ?? result.ComputerAction?.OsInputAccepted
                            ?? result.Success;
        return result with
        {
            ComputerAction = computerAction,
            Effect = new ToolEffectResult(
                nativeSuccess,
                false,
                false,
                failureCode,
                verification.Reason,
                verification.Disposition == VerificationDisposition.Inconclusive,
                evidence,
                result.Effect?.ExitCode,
                result.Effect?.StatusCode,
                result.Effect?.SideEffects)
        };
    }

    private static bool IsVerifiedForPresentation(ToolResult result)
    {
        if (!result.Success)
            return false;
        if (result.ComputerAction is { } computer)
            return computer.TaskCompleted && computer.EffectVerified;
        if (result.Effect is { } effect)
            return effect.Verified;
        return true;
    }

    private static void UpsertPinnedContext(
        List<GatewayMessage> messages,
        string pinnedContext)
    {
        var existing = messages.FindIndex(m =>
            string.Equals(m.Role, "system", StringComparison.Ordinal)
            && m.Content is string text
            && text.StartsWith(DurableTaskRuntime.ContextMarker, StringComparison.Ordinal));
        var message = new GatewayMessage { Role = "system", Content = pinnedContext };
        if (existing >= 0)
        {
            messages[existing] = message;
            return;
        }

        var insertAt = 0;
        while (insertAt < messages.Count
               && string.Equals(messages[insertAt].Role, "system", StringComparison.Ordinal))
            insertAt++;
        messages.Insert(insertAt, message);
    }

    private static string VerificationLine(DurableCompletionDecision completion)
    {
        var evidence = completion.EvidenceIds.Count == 0
            ? "none"
            : string.Join(", ", completion.EvidenceIds);
        return $"Runtime verification: {completion.Message} Evidence: {evidence}.";
    }

    /// <summary>Verification appended after an already-streamed body — the leading
    /// blank line separates it from the model's last streamed token.</summary>
    private static string VerificationSuffix(DurableCompletionDecision completion) =>
        $"\n\n{VerificationLine(completion)}";

    private static string BuildVerifiedFinalResponse(
        string modelContent,
        DurableCompletionDecision completion)
    {
        var verified = VerificationLine(completion);
        return string.IsNullOrWhiteSpace(modelContent)
            ? verified
            : $"{modelContent.Trim()}\n\n{verified}";
    }

    internal static string ExtractScope(IMcpTool tool, string argumentsJson)
    {
        if (tool.Category != ToolCategory.FileSystem)
            return "*";
        try
        {
            using var doc = JsonDocument.Parse(argumentsJson);
            if (doc.RootElement.ValueKind == JsonValueKind.Object)
            {
                if (doc.RootElement.TryGetProperty("path", out var p) && p.ValueKind == JsonValueKind.String)
                    return p.GetString() ?? "*";
                if (doc.RootElement.TryGetProperty("directory", out var d) && d.ValueKind == JsonValueKind.String)
                    return d.GetString() ?? "*";
            }
        }
        catch (JsonException)
        {
        }
        return "*";
    }

    private static string SummarizeArgs(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            return SensitiveDataRedactor.Redact(
                JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (JsonException)
        {
            return SensitiveDataRedactor.Redact(json);
        }
    }

    private async Task LogExecutionAsync(
        string conversationId, GatewayToolCall toolCall, string permission,
        string? result, string? error, long? durationMs, CancellationToken ct)
    {
        try
        {
            await _store.InsertToolExecutionAsync(new ToolExecution
            {
                Id = $"{toolCall.Id}-{Guid.NewGuid():N}",
                ConversationId = conversationId,
                ToolName = toolCall.Function.Name,
                ArgumentsJson = SensitiveDataRedactor.RedactJson(toolCall.Function.Arguments),
                Result = SensitiveDataRedactor.Redact(
                    result is { Length: > 2000 } ? result[..2000] : result),
                Permission = permission,
                DurationMs = durationMs,
                Error = SensitiveDataRedactor.Redact(error),
                ExecutedAt = DateTimeOffset.UtcNow
            }, ct);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to log tool execution");
        }
    }
}
