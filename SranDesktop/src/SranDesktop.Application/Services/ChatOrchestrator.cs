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
    /// <summary>Default safety caps for a top-level turn. These are now a SOFT
    /// budget (Phase A PART 1/6): reaching them pauses the turn with its tool
    /// history intact and offers Continue, rather than abandoning the task — so
    /// the ceiling can be much higher than the old 25 without risking a runaway.
    /// A nested run (run_subagent, Phase 12 F) constructs its own instance with
    /// tighter caps via the optional constructor parameters.</summary>
    public const int DefaultMaxIterations = 100;
    public static readonly TimeSpan DefaultMaxDuration = TimeSpan.FromMinutes(20);

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
        IToolScopeResolver? scopeResolver = null)
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

            await foreach (var frame in _gateway.StreamCompletionAsync(request, ct))
            {
                foreach (var evt in parser.Parse(frame.Data))
                {
                    switch (evt)
                    {
                        case AgentContentDeltaEvent delta:
                            assistantContent.Append(delta.Text);
                            yield return delta;
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
                    yield return errorEvent with
                    {
                        Message = errorEvent.Message +
                                  " This conversation is too long for the model — start a new chat to continue."
                    };
                    yield break;
                }

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
                // Final answer — no tools requested this turn.
                yield return new AgentStreamDoneEvent();
                yield break;
            }

            yield return new AgentToolCallsEvent(pendingToolCalls);

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

                await foreach (var evt in ExecuteToolCallAsync(toolCall, messages, conversationId, ct))
                    yield return evt;
            }

            if (pausedAtDeadline)
            {
                yield return new AgentPausedEvent(TimeLimitMessage);
                yield break;
            }
        }

        // The cap is a checkpoint, not a cliff: the accumulated tool history is
        // intact in `messages` (the caller's list), so the UI can offer Continue
        // and re-enter this loop to resume the task (PART 1/6).
        yield return new AgentPausedEvent(iteration >= _maxIterations
            ? $"Paused: reached the {_maxIterations}-step limit. The task can be continued."
            : TimeLimitMessage);
    }

    private async IAsyncEnumerable<AgentStreamEvent> ExecuteToolCallAsync(
        GatewayToolCall toolCall,
        List<GatewayMessage> messages,
        string conversationId,
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

        // Folder-scoped consent (PART 4): the resolver maps writes to their
        // project folder and shell to its cwd; falls back to the exact-path
        // scope when no resolver is wired (tests).
        var scope = _scopeResolver?.Resolve(tool, argumentsJson, conversationId)
                    ?? ExtractScope(tool, argumentsJson);
        var decision = await _permissions.CheckAsync(tool, scope, ct);

        if (decision == PermissionDecision.NeedsConsent)
        {
            yield return new AgentToolConsentRequestEvent(
                toolCall.Id, tool.Name, SummarizeArgs(argumentsJson), tool.DefaultPermission);

            var response = ConsentRequested is not null
                ? await ConsentRequested(new ToolConsentRequest(
                    toolCall.Id, tool.Name, tool.Description, tool.Category, argumentsJson, scope))
                : ToolConsentResponse.Deny; // no UI handler → safe default

            if (response.Denied)
            {
                messages.Add(ToolResultMessage(toolCall.Id, tool.Name,
                    "Error: the user denied permission for this tool call."));
                yield return new AgentToolResultEvent(toolCall.Id, tool.Name, argumentsJson, false,
                    "Permission denied by user", 0);
                await LogExecutionAsync(conversationId, toolCall, "denied", null, null, null, ct);
                yield break;
            }

            if (response.AllowAlways)
                await _permissions.PersistDecisionAsync(tool.Name, scope, "allow_always", ct);
            else if (response.AllowOnce)
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

        var stopwatch = Stopwatch.StartNew();
        ToolResult result;
        try
        {
            // Carry the active conversation to the tool via the ambient shell
            // session (flows through AsyncLocal) so per-conversation shell state
            // — the retained working directory — resolves correctly (PART 3).
            if (_shellSession is not null)
                _shellSession.CurrentConversationId = conversationId;

            using var args = JsonDocument.Parse(
                string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
            result = await tool.ExecuteAsync(args.RootElement, ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
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

        var content = result.Success ? result.TruncatedContent : $"Error: {result.Error}";
        messages.Add(ToolResultMessage(toolCall.Id, tool.Name, content));

        // Screenshots ride back as a vision message (see ScreenshotMessage —
        // shared with ScreenshotPruner so old ones can be pruned safely).
        if (result.Success && result.ImageDataUri is { Length: > 0 } dataUri)
            messages.Add(ScreenshotMessage.Create(tool.Name, dataUri));

        yield return new AgentToolResultEvent(
            toolCall.Id, tool.Name, argumentsJson, result.Success,
            content.Length > 200 ? content[..200] + "..." : content,
            stopwatch.ElapsedMilliseconds,
            FullResult: content,
            ImageDataUri: result.ImageDataUri);

        var wasAutoApproved = decision == PermissionDecision.Auto;
        await LogExecutionAsync(conversationId, toolCall,
            wasAutoApproved ? "auto" : "allowed",
            result.Success ? result.TruncatedContent : null,
            result.Error, stopwatch.ElapsedMilliseconds, ct);
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
            return JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (JsonException)
        {
            return json;
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
                ArgumentsJson = toolCall.Function.Arguments,
                Result = result is { Length: > 2000 } ? result[..2000] : result,
                Permission = permission,
                DurationMs = durationMs,
                Error = error,
                ExecutedAt = DateTimeOffset.UtcNow
            }, ct);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to log tool execution");
        }
    }
}
