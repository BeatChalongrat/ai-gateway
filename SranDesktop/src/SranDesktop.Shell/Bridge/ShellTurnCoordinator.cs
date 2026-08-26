using System.Collections.Concurrent;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Shell.Bridge;

/// <summary>
/// Phase 3 §3 — one agent turn, host-side. The page submits `turn.submit`
/// (no tool names, no paths); this drives the real
/// <see cref="IChatOrchestrator"/> and translates each
/// <see cref="AgentStreamEvent"/> into a `turn.event` frame shaped exactly
/// like the SSE events the web renderer already consumes (message_start,
/// content_delta, reasoning_delta, analysis_status, tool_activity,
/// message_complete, error). The renderer is untouched; only the transport
/// changed.
///
/// Consent round-trips through the bridge: the orchestrator's awaited
/// `ConsentRequested` delegate posts `consent.request`, then blocks on a
/// promptId-keyed TaskCompletionSource that `consent.respond` completes — the
/// map the plan says a bridge "must build itself" because the .NET consent
/// contract answers by return value, not a registry. The real
/// <see cref="IPermissionService"/> and <see cref="ToolDangerClassifier"/> do
/// the deciding; Phase 1's deny-all stub is gone.
///
/// This is transport + one turn. No local persistence, no chat-edge sync, no
/// UI — those are later phases. A turn here is live and audited but ephemeral.
/// </summary>
public sealed class ShellTurnCoordinator
{
    private readonly IChatOrchestrator _orchestrator;
    private readonly ISettingsService _settings;
    private readonly IMcpToolHost _toolHost;
    private readonly IConversationStore _store;
    private readonly AgentTurnAssembler _assembler;
    private readonly IGatewayApiClient _gateway;
    private readonly IChatEdgeApiClient _api;
    // Mirrors the local transcript to chat-edge so a page RELOAD (which reads
    // chat-edge, where shell turns otherwise leave message_count=0) shows the
    // conversation. Optional: null in unit tests that don't exercise sync.
    private readonly IAgentConversationSyncService? _sync;
    private readonly ILogger<ShellTurnCoordinator> _logger;

    private BridgeChannel? _bridge;

    // One in-flight turn per conversation.
    private readonly ConcurrentDictionary<string, CancellationTokenSource> _turns = new();
    // Pending consent prompts, keyed by the id the page echoes back.
    private readonly ConcurrentDictionary<string, TaskCompletionSource<ToolConsentResponse>> _consents = new();
    // Pending consent REQUESTS keyed by promptId, so a reload mid-consent can be
    // re-posted on ui.ready (consent.request is not in the turn.event buffer).
    // Each carries the conversation it belongs to (from _turnConversation).
    private readonly ConcurrentDictionary<string, PendingConsent> _pendingConsents = new();
    private readonly record struct PendingConsent(string ConversationId, object Payload);
    // Flows the current turn's conversation into the inline consent callback
    // (the orchestrator awaits ConsentRequested within RunTurnAsync's context).
    private static readonly AsyncLocal<string?> _turnConversation = new();
    // Per-conversation event log for reload recovery (plan addendum): each
    // turn.event gets a monotonic seq and is buffered so a reloaded page can
    // replay from seq+1 via ui.ready. Retained until the next turn starts, so
    // a reload just after completion still recovers the whole answer.
    private readonly ConcurrentDictionary<string, ConversationLog> _logs = new();

    private sealed class ConversationLog
    {
        public readonly object Gate = new();
        public long Seq;
        public readonly List<BufferedEvent> Events = new();
    }

    private readonly record struct BufferedEvent(long Seq, string Event, object Data);

    public ShellTurnCoordinator(
        IChatOrchestrator orchestrator,
        ISettingsService settings,
        IMcpToolHost toolHost,
        IConversationStore store,
        AgentTurnAssembler assembler,
        IGatewayApiClient gateway,
        IChatEdgeApiClient api,
        ILogger<ShellTurnCoordinator> logger,
        IAgentConversationSyncService? sync = null)
    {
        _orchestrator = orchestrator;
        _settings = settings;
        _toolHost = toolHost;
        _store = store;
        _assembler = assembler;
        _gateway = gateway;
        _api = api;
        _logger = logger;
        _sync = sync;
    }

    /// <summary>Wire the bridge's inbound events and take over consent from the
    /// Phase 1 deny-all stub. Idempotent per bridge instance.</summary>
    /// <summary>DEBUG/test only: fired with the promptId right after a
    /// consent.request is posted to the page, so a scripted test can post a
    /// real consent.respond back over the bridge (what a Phase 4 consent card
    /// will do). Never used in production.</summary>
    public event Action<string>? ConsentRequestPosted;

    public void Attach(BridgeChannel bridge)
    {
        _bridge = bridge;
        bridge.TurnSubmitReceived += OnTurnSubmit;
        bridge.TurnCancelReceived += OnTurnCancel;
        bridge.ConsentRespondReceived += OnConsentRespond;
        bridge.UiReadyReceived += OnUiReady;
        _orchestrator.ConsentRequested += HandleConsentAsync;
        _logger.LogInformation("Turn coordinator attached: real consent gate active (deny-all stub replaced)");
    }

    // ------------------------------------------------------------------
    // turn.submit → RunAsync → turn.event frames
    // ------------------------------------------------------------------

    private void OnTurnSubmit(TurnSubmitPayload payload)
    {
        var conversationId = payload.ConversationId;
        var cts = new CancellationTokenSource();
        // Cancel any prior in-flight turn for this conversation, but let ITS
        // finally dispose it (disposing here could race the orchestrator still
        // reading its token). Overwriting the key means the old turn's
        // compare-and-remove will no longer match, so it disposes itself
        // without touching this new turn's registration.
        if (_turns.TryGetValue(conversationId, out var old))
            old.Cancel();
        _turns[conversationId] = cts;
        _ = Task.Run(() => RunTurnAsync(payload, cts, cts.Token));
    }

    private async Task RunTurnAsync(TurnSubmitPayload payload, CancellationTokenSource cts, CancellationToken ct)
    {
        var conversationId = payload.ConversationId;
        var bridge = _bridge!;
        var model = string.IsNullOrWhiteSpace(payload.ModelId)
            ? (string.IsNullOrWhiteSpace(_settings.Current.DefaultModel) ? "auto" : _settings.Current.DefaultModel)
            : payload.ModelId!;

        // A stable assistant-message id so the page can align its optimistic
        // row; the page also renders fine without alignment (finding: the id
        // swap is just a rename).
        _turnConversation.Value = conversationId; // flows into the inline consent callback
        var messageId = "asst_" + Guid.NewGuid().ToString("N");
        // A new turn resets the conversation's event log: seq restarts at 1 and
        // the prior turn's buffer is dropped (a resume only ever recovers the
        // current turn).
        var log = new ConversationLog();
        _logs[conversationId] = log;
        Emit(conversationId, "message_start", new { message_id = messageId, conversation_id = conversationId });

        var messages = await BuildMessagesAsync(conversationId, payload.Text, ct);
        _logger.LogInformation("TURN START conv={Conv} model={Model} msgId={MsgId} textLen={Len} attachments={Att}",
            conversationId, model, messageId, payload.Text.Length, payload.AttachmentHandles.Count);
        // Accumulate the assistant's visible text so the turn can be persisted to
        // the local store — the history the NEXT turn loads (parity report §3).
        var assistantContent = new System.Text.StringBuilder();
        // Turn-parity diagnostic: the exact outbound array shape (roles + content
        // lengths; no content dumped). Lets the parity report compare the shell's
        // array against the WPF app's without a network capture.
        _logger.LogInformation("TURN OUTBOUND conv={Conv}: {Count} msg → [{Shape}]",
            conversationId, messages.Count,
            string.Join(" , ", messages.Select(m =>
                $"{m.Role}:{(m.Content is string s ? s.Length + "ch" : m.Content is null ? "null" : "blocks")}")));
        // §3: the orchestrator mutates `messages`, appending the tool chain after
        // this point; everything from here on is what to persist as tool history.
        var chainStart = messages.Count;
        // §4: a conversation with no prior turns ([system, user] only) is titled
        // after this turn completes, exactly as the WPF app does.
        var isFirstTurn = messages.Count == 2;

        // Phase 4: the two off-band streaming surfaces the WPF app also wires —
        // run_command live output and run_subagent nested progress. Discovered
        // via the tool host (the same GetRegisteredTools().OfType<>() the WPF
        // ChatViewModel uses) and unsubscribed in finally. `_currentRunCommandId`
        // and `_currentSubagentId` route the off-band events to the right card.
        string? currentRunCommandId = null;
        string? currentSubagentId = null;
        var outputTool = _toolHost.GetRegisteredTools().OfType<ISupportsOutputStreaming>().FirstOrDefault();
        var nestedTool = _toolHost.GetRegisteredTools().OfType<ISupportsNestedProgress>().FirstOrDefault();

        void OnOutput(ToolOutputChunk chunk)
        {
            var id = Volatile.Read(ref currentRunCommandId);
            if (id is not null)
                Emit(conversationId, "tool_output", new { id, chunk = chunk.Text });
        }
        void OnNested(AgentStreamEvent nev)
        {
            var parentId = Volatile.Read(ref currentSubagentId);
            if (parentId is null) return;
            switch (nev)
            {
                case AgentToolCallsEvent ntc:
                    foreach (var call in ntc.ToolCalls)
                        Emit(conversationId, "tool_activity", new
                        {
                            id = call.Id, tool = call.Function.Name, phase = "started", parentId,
                            argsSummary = ToolCardFormatter.Humanize(call.Function.Name, call.Function.Arguments),
                        });
                    break;
                case AgentToolResultEvent ntr:
                    Emit(conversationId, "tool_activity", new
                    {
                        id = ntr.ToolCallId, tool = ntr.ToolName, phase = "finished", parentId,
                        success = ntr.Success, durationMs = ntr.DurationMs,
                        resultPreview = ntr.ResultPreview,
                    });
                    break;
            }
        }
        if (outputTool is not null) outputTool.OutputStreamed += OnOutput;
        if (nestedTool is not null) nestedTool.NestedProgress += OnNested;

        var analysisOpen = false;
        try
        {
            await foreach (var ev in _orchestrator.RunAsync(model, messages, conversationId, contextWindow: null, ct))
            {
                switch (ev)
                {
                    case AgentContentDeltaEvent d:
                        assistantContent.Append(d.Text);
                        Emit(conversationId, "content_delta", new { delta = d.Text });
                        break;
                    case AgentReasoningDeltaEvent r:
                        Emit(conversationId, "reasoning_delta", new { delta = r.Text });
                        break;
                    case AgentAnalysisStatusEvent a:
                        analysisOpen = a.Status == "started";
                        Emit(conversationId, "analysis_status", new { status = a.Status });
                        break;
                    case AgentStatusEvent s:
                        // Phase marker ("Working… (step n/max)"). The renderer
                        // shows it only while content is empty (like WPF).
                        Emit(conversationId, "status", new { text = s.Status, iteration = s.Iteration, maxIterations = s.MaxIterations });
                        break;
                    case AgentToolCallsEvent tc:
                        foreach (var call in tc.ToolCalls)
                        {
                            // update_plan is intercepted, not a card — parse it
                            // into a plan_update (WPF does the same).
                            if (call.Function.Name == "update_plan")
                            {
                                var plan = ParsePlan(call.Function.Arguments);
                                if (plan is not null)
                                    Emit(conversationId, "plan_update", new { steps = plan });
                                continue;
                            }
                            if (call.Function.Name == "run_command") Volatile.Write(ref currentRunCommandId, call.Id);
                            if (call.Function.Name == "run_subagent") Volatile.Write(ref currentSubagentId, call.Id);
                            Emit(conversationId, "tool_activity", new
                            {
                                id = call.Id, tool = call.Function.Name, phase = "started",
                                argsSummary = ToolCardFormatter.Humanize(call.Function.Name, call.Function.Arguments),
                            });
                        }
                        break;
                    case AgentToolResultEvent tr:
                        if (tr.ToolName == "update_plan") break; // no card for the plan tool
                        if (tr.ToolName == "run_command") Volatile.Write(ref currentRunCommandId, null);
                        if (tr.ToolName == "run_subagent") Volatile.Write(ref currentSubagentId, null);
                        var isCode = tr.ToolName == "run_python";
                        Emit(conversationId, "tool_activity", new
                        {
                            id = tr.ToolCallId, tool = tr.ToolName, phase = "finished",
                            success = tr.Success, durationMs = tr.DurationMs,
                            input = ToolCardFormatter.BuildInput(tr.ArgumentsJson),
                            resultPreview = tr.FullResult ?? tr.ResultPreview,
                            isCodeRun = isCode,
                            codeText = isCode ? ToolCardFormatter.ExtractCode(tr.ArgumentsJson) : null,
                            imageDataUri = tr.ImageDataUri,
                        });
                        break;
                    case AgentFallbackEvent f:
                        Emit(conversationId, "fallback_info",
                            new { requested_model = f.RequestedModel, actual_model = f.ActualModel, reason = f.Reason });
                        break;
                    case AgentReasoningNoticeEvent n:
                        Emit(conversationId, "reasoning_notice", new { model = n.Model, reason = n.Reason });
                        break;
                    case AgentErrorEvent e:
                        Emit(conversationId, "error", new { code = e.Code ?? "agent_error", message = e.Message });
                        return; // terminal
                    case AgentPausedEvent p:
                        // Budget checkpoint — a real paused payload (Continue pill).
                        _logger.LogInformation("TURN PAUSED conv={Conv}: {Reason}", conversationId, p.Reason);
                        await PersistTurnAsync(conversationId, payload.Text, messages, chainStart, assistantContent.ToString(), messageId, ct);
                        Emit(conversationId, "paused", new { reason = p.Reason });
                        Emit(conversationId, "message_complete", new { message_id = messageId });
                        return;
                    case AgentStreamDoneEvent:
                        if (analysisOpen)
                            Emit(conversationId, "analysis_status", new { status = "completed" });
                        await PersistTurnAsync(conversationId, payload.Text, messages, chainStart, assistantContent.ToString(), messageId, ct);
                        // §4 — auto-title the fresh conversation (fire-and-forget,
                        // like the WPF app: it must not delay message_complete).
                        if (isFirstTurn)
                            _ = GenerateTitleAsync(conversationId, payload.Text, assistantContent.ToString());
                        Emit(conversationId, "message_complete", new { message_id = messageId });
                        _logger.LogInformation("TURN DONE conv={Conv}", conversationId);
                        return;
                    default:
                        break;
                }
            }
            // Enumeration ended without an explicit done (defensive).
            Emit(conversationId, "message_complete", new { message_id = messageId });
        }
        catch (OperationCanceledException)
        {
            _logger.LogInformation("TURN CANCELLED conv={Conv}", conversationId);
            // The page already went to a cancelled state via its AbortController;
            // no error frame — a cancel is not an error. But the work the user
            // just watched (tool calls, output, partial text) must NOT be thrown
            // away: persist it exactly like a done/paused turn so "continue"
            // resumes from what actually happened, not from nothing. A tool that
            // was still running when cancelled leaves a tool_call with no result;
            // balance it first, or the next turn replays an unbalanced chain and
            // the gateway rejects it. Persist off the cancelled token so the
            // store writes actually complete.
            AppendCancelledToolResults(messages, chainStart);
            await PersistTurnAsync(conversationId, payload.Text, messages, chainStart,
                assistantContent.ToString(), messageId, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "TURN FAILED conv={Conv}", conversationId);
            Emit(conversationId, "error", new { code = "host_exception", message = "The turn failed in the host." });
        }
        finally
        {
            if (outputTool is not null) outputTool.OutputStreamed -= OnOutput;
            if (nestedTool is not null) nestedTool.NestedProgress -= OnNested;
            // Remove ONLY if this turn's own cts is still the registered one —
            // a newer turn for the same conversation may have replaced it (and
            // owns its own disposal). The KeyValuePair overload makes the
            // compare-and-remove atomic.
            _turns.TryRemove(new KeyValuePair<string, CancellationTokenSource>(conversationId, cts));
            cts.Dispose();
        }
    }

    /// <summary>update_plan args → the plan_update step list. Reuses the
    /// Application-layer PlanParser (same parser the live tool and the WPF app
    /// use), so the shell's checklist matches exactly.</summary>
    private static object[]? ParsePlan(string argumentsJson)
    {
        var steps = PlanParser.Parse(argumentsJson);
        if (steps.Count == 0) return null;
        return steps.Select(s => (object)new { text = s.Step, status = s.Status }).ToArray();
    }

    /// <summary>Assign the next seq for this conversation, buffer the event for
    /// reload recovery, and post it. seq lives on the turn.event ENVELOPE, not
    /// inside `data`, so the renderer's event payload is unchanged.</summary>
    private void Emit(string conversationId, string eventName, object data)
    {
        var log = _logs.GetOrAdd(conversationId, _ => new ConversationLog());
        long seq;
        lock (log.Gate)
        {
            seq = ++log.Seq;
            log.Events.Add(new BufferedEvent(seq, eventName, data));
        }
        _bridge!.PostTurnEvent(new { conversationId, seq, @event = eventName, data });
    }

    /// <summary>Reload recovery (plan addendum): the page came back and told us
    /// which conversation it is showing and the last seq it rendered. If a turn
    /// is live (or just completed) for it, replay every buffered event after
    /// that seq. If nothing is buffered, reply nothing — the page behaves as
    /// today.</summary>
    private void OnUiReady(UiReadyPayload payload)
    {
        if (payload.ConversationId is null || !_logs.TryGetValue(payload.ConversationId, out var log))
            return;
        List<BufferedEvent> replay;
        lock (log.Gate)
            replay = log.Events.Where(e => e.Seq > payload.LastSeq).ToList();
        if (replay.Count == 0)
        {
            _logger.LogInformation("RESUME conv={Conv}: page at seq {Seq}, nothing to replay", payload.ConversationId, payload.LastSeq);
            return;
        }
        _logger.LogInformation("RESUME conv={Conv}: replaying {Count} event(s) from seq {From} (page was at {Was})",
            payload.ConversationId, replay.Count, payload.LastSeq + 1, payload.LastSeq);
        foreach (var e in replay)
            _bridge!.PostTurnEvent(new { conversationId = payload.ConversationId, seq = e.Seq, @event = e.Event, data = e.Data });

        // A tool call may be blocked awaiting consent right now. consent.request
        // is NOT in the turn.event buffer (it's a separate outbound message), so
        // re-post any pending consent for this conversation — the host-side
        // TaskCompletionSource is still awaiting, so the reloaded page can still
        // answer it and unblock the tool.
        foreach (var kv in _pendingConsents)
        {
            if (kv.Value.ConversationId == payload.ConversationId)
            {
                _logger.LogInformation("RESUME conv={Conv}: re-posting pending consent {Prompt}", payload.ConversationId, kv.Key);
                _bridge!.PostConsentRequest(kv.Value.Payload);
            }
        }
    }

    /// <summary>
    /// The turn's message list, at PARITY with the WPF app (SHELL_TURN_PARITY
    /// _REPORT.md §2): the shared <see cref="AgentTurnAssembler"/> builds the full
    /// system prompt + extras (artifacts, file manifest, agent memory, skills,
    /// writing style, personal instructions), then the prior turns load from the
    /// local store as history, then the current user text. Before this the shell
    /// sent only <c>[system(2 lines), user]</c> — no history, none of the extras.
    ///
    /// Seams the shell supplies as absent (report §2): there is no project
    /// selection or image/video toggle on the bridge, so ActiveProject is null and
    /// the mode flags false. Durable tool-chain replay is not carried yet — the
    /// visible transcript (user + assistant text) is, which is what multi-turn
    /// memory needs; tool-chain persistence is named as remaining work (§3).
    /// </summary>
    private async Task<List<GatewayMessage>> BuildMessagesAsync(
        string conversationId, string text, CancellationToken ct)
    {
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var project = await ResolveProjectAsync(conversationId, ct); // §2 — from chat-edge
        var inputs = new TurnPromptInputs(
            HomeDirectory: home,
            ConversationId: conversationId,
            ActiveProject: project,
            ImageMode: false,
            VideoMode: false,
            MemoryEnabled: true,
            LastUserText: text,
            // The shell displays web-chat, whose renderer detects artifacts from a
            // code block's language tag, NOT a :::artifact fence (closing round §1).
            ArtifactDialect: ArtifactDialect.WebChatCodeBlock);

        var system = await _assembler.BuildSystemPromptAsync(inputs, ct);
        var messages = new List<GatewayMessage> { new() { Role = "system", Content = system } };

        try
        {
            // §3 — replay the full durable chain: user/assistant TEXT and the
            // agent's tool chain (assistant-with-tool_calls + role:tool results),
            // so turn N sees what turn N-1 ran/read/returned, not just its summary.
            foreach (var m in await _store.GetMessagesAsync(conversationId, ct))
            {
                var gm = ToGatewayMessage(m);
                if (gm is not null) messages.Add(gm);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not load history for {Conv}", conversationId);
        }

        messages.Add(new GatewayMessage { Role = "user", Content = text });
        return messages;
    }

    /// <summary>A turn cancelled mid-tool leaves the orchestrator's message list
    /// with an assistant tool_call that never got its role:"tool" answer. Left
    /// as-is, the next turn replays that dangling call and the gateway rejects
    /// the whole request (every tool_call must have a matching tool result).
    /// Append a synthetic "cancelled" result for each unanswered call so the
    /// persisted chain is balanced AND the model, on "continue", can see which
    /// tool it was interrupted in. Appends in place; no-op when nothing dangles.</summary>
    internal static void AppendCancelledToolResults(List<GatewayMessage> messages, int chainStart)
    {
        if (chainStart < 0 || chainStart >= messages.Count) return;
        var answered = new HashSet<string>(StringComparer.Ordinal);
        for (var i = chainStart; i < messages.Count; i++)
            if (messages[i].Role == "tool" && messages[i].ToolCallId is { Length: > 0 } id)
                answered.Add(id);

        var synthesized = new List<GatewayMessage>();
        for (var i = chainStart; i < messages.Count; i++)
        {
            var gm = messages[i];
            if (gm.Role != "assistant" || gm.ToolCalls is not { Count: > 0 }) continue;
            foreach (var call in gm.ToolCalls)
                if (!answered.Contains(call.Id))
                    synthesized.Add(new GatewayMessage
                    {
                        Role = "tool",
                        ToolCallId = call.Id,
                        Name = call.Function.Name,
                        Content = "(This tool call was cancelled by the user before it returned.)",
                    });
        }
        messages.AddRange(synthesized);
    }

    /// <summary>Persist this turn's user + assistant text to the local store so
    /// the NEXT turn (this session or after a restart) carries it as history. The
    /// shell's conversations are <see cref="ConversationSource.Local"/> — they
    /// talk to the gateway, not chat-edge, so they are genuinely local-only.</summary>
    private async Task PersistTurnAsync(
        string conversationId, string userText, List<GatewayMessage> messages, int chainStart,
        string assistantText, string messageId, CancellationToken ct)
    {
        try
        {
            if (await _store.GetConversationAsync(conversationId, ct) is null)
                await _store.UpsertConversationAsync(new Conversation
                {
                    Id = conversationId,
                    CreatedAt = DateTimeOffset.UtcNow,
                    UpdatedAt = DateTimeOffset.UtcNow,
                    Source = ConversationSource.Local,
                }, ct);

            var order = await _store.GetNextSortOrderAsync(conversationId, ct);
            var now = DateTimeOffset.UtcNow;
            var rows = new List<Message>
            {
                new() { Id = "u_" + Guid.NewGuid().ToString("N"), ConversationId = conversationId,
                        Role = MessageRole.User, Content = userText, CreatedAt = now, SortOrder = order++ },
            };

            // §3 — the durable tool chain the orchestrator appended to `messages`
            // during the turn. Persist it verbatim (the orchestrator already
            // bounds tool-result size — ToolResult.TruncatedContent — so we inherit
            // the WPF path's limit rather than inventing a new one).
            for (var i = chainStart; i < messages.Count; i++)
            {
                var gm = messages[i];
                if (gm.Role == "assistant" && gm.ToolCalls is { Count: > 0 })
                    rows.Add(new Message
                    {
                        Id = "a_" + Guid.NewGuid().ToString("N"), ConversationId = conversationId,
                        Role = MessageRole.Assistant, Content = gm.Content as string,
                        ToolCallsJson = JsonSerializer.Serialize(gm.ToolCalls),
                        CreatedAt = now, SortOrder = order++,
                    });
                else if (gm.Role == "tool")
                    rows.Add(new Message
                    {
                        Id = "t_" + Guid.NewGuid().ToString("N"), ConversationId = conversationId,
                        Role = MessageRole.Tool, Content = gm.Content as string ?? "",
                        ToolCallId = gm.ToolCallId, Name = gm.Name,
                        CreatedAt = now, SortOrder = order++,
                    });
            }

            if (assistantText.Length > 0)
                rows.Add(new Message { Id = messageId, ConversationId = conversationId,
                        Role = MessageRole.Assistant, Content = assistantText, CreatedAt = now, SortOrder = order++ });
            await _store.UpsertMessagesAsync(rows, ct);

            // Cross-reload fix: the local store now holds this turn, but a page
            // reload reads chat-edge, where shell turns left message_count=0.
            // Queue a debounced mirror to chat-edge (idempotent by client_id,
            // and gated by the user's SyncAgentConversations setting) so the
            // reloaded transcript is no longer empty. Fire-and-forget — it must
            // not delay the turn.
            _sync?.QueueConversationSync(conversationId);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not persist shell turn for {Conv}", conversationId);
        }
    }

    /// <summary>Converts a stored row back to a gateway message, preserving the
    /// tool chain (§3): an assistant row with tool_calls JSON, a role:tool result
    /// row, or plain user/assistant text.</summary>
    private static GatewayMessage? ToGatewayMessage(Message m)
    {
        switch (m.Role)
        {
            case MessageRole.User:
                return string.IsNullOrWhiteSpace(m.Content) ? null
                    : new GatewayMessage { Role = "user", Content = m.Content };
            case MessageRole.Assistant:
                List<GatewayToolCall>? toolCalls = null;
                if (!string.IsNullOrWhiteSpace(m.ToolCallsJson))
                {
                    try { toolCalls = JsonSerializer.Deserialize<List<GatewayToolCall>>(m.ToolCallsJson); }
                    catch (JsonException) { /* corrupt row → drop the tool_calls, keep any text */ }
                }
                if (toolCalls is null && string.IsNullOrWhiteSpace(m.Content))
                    return null;
                return new GatewayMessage
                {
                    Role = "assistant",
                    Content = string.IsNullOrEmpty(m.Content) ? null : m.Content,
                    ToolCalls = toolCalls,
                };
            case MessageRole.Tool:
                return new GatewayMessage
                {
                    Role = "tool", ToolCallId = m.ToolCallId, Name = m.Name, Content = m.Content ?? "",
                };
            default:
                return null;
        }
    }

    /// <summary>§2 — the conversation's project, resolved from chat-edge (the page
    /// created the conversation there, so its project_id + the project's
    /// instructions live on the server). Null when there is no project or the
    /// server is unreachable — the turn just runs without project context.</summary>
    private async Task<Project?> ResolveProjectAsync(string conversationId, CancellationToken ct)
    {
        try
        {
            var conv = await _api.GetConversationAsync(conversationId, ct);
            if (string.IsNullOrEmpty(conv.ProjectId))
                return null;
            var projects = await _api.ListProjectsAsync(ct);
            var p = projects.Projects.FirstOrDefault(x => x.Id == conv.ProjectId);
            return p is null ? null : new Project { Id = p.Id, Name = p.Name ?? "", Instructions = p.Instructions };
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Could not resolve project for {Conv}", conversationId);
            return null;
        }
    }

    /// <summary>§4 — auto-title a fresh conversation exactly as the WPF app does
    /// (ChatViewModel.TryGenerateTitleAsync): a tiny gateway completion from the
    /// first user + assistant text, then rename on chat-edge (the conversation
    /// exists there — the page created it — so the sidebar picks up the name).</summary>
    private async Task GenerateTitleAsync(string conversationId, string firstUser, string firstReply)
    {
        try
        {
            if (firstUser.Length == 0)
                return;
            var request = new GatewayCompletionRequest
            {
                Model = "auto",
                Messages = new List<GatewayMessage>
                {
                    new() { Role = "system", Content =
                        "Generate a very short title (3-6 words, no quotes, no trailing period, " +
                        "same language as the conversation) for this chat. Reply with the title only." },
                    new() { Role = "user", Content =
                        $"User: {TitleTruncate(firstUser, 500)}\n\nAssistant: {TitleTruncate(firstReply, 500)}" },
                },
            };
            var parser = new GatewayChunkParser();
            var title = new System.Text.StringBuilder();
            await foreach (var frame in _gateway.StreamCompletionAsync(request))
                foreach (var evt in parser.Parse(frame.Data))
                {
                    if (evt is AgentContentDeltaEvent d) title.Append(d.Text);
                    else if (evt is AgentErrorEvent) return;
                }
            var cleaned = title.ToString().Trim().Trim('"', '“', '”').Trim();
            if (cleaned.Length is 0 or > 80)
                return;
            await _api.RenameConversationAsync(conversationId, cleaned);
            _logger.LogInformation("TURN TITLE conv={Conv}: {Title}", conversationId, cleaned);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Title generation skipped for {Conv}", conversationId);
        }
    }

    private static string TitleTruncate(string value, int max) =>
        value.Length <= max ? value : value[..max] + "...";

    // ------------------------------------------------------------------
    // turn.cancel
    // ------------------------------------------------------------------

    private void OnTurnCancel(TurnCancelPayload payload)
    {
        if (_turns.TryGetValue(payload.ConversationId, out var cts))
        {
            _logger.LogInformation("TURN CANCEL requested conv={Conv}", payload.ConversationId);
            cts.Cancel();
        }
    }

    // ------------------------------------------------------------------
    // consent.request (host→page) / consent.respond (page→host)
    // ------------------------------------------------------------------

    private async Task<ToolConsentResponse> HandleConsentAsync(ToolConsentRequest request)
    {
        var promptId = "consent_" + Guid.NewGuid().ToString("N");
        var conversationId = _turnConversation.Value;
        var danger = ToolDangerClassifier.Classify(request.ToolName, request.Category);
        var tcs = new TaskCompletionSource<ToolConsentResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
        _consents[promptId] = tcs;

        // The consent card needs a one-line summary AND expanded "name: value"
        // lines (the WPF dialog's args box), plus the category (icon) and the
        // description. The LOG never carries any of it (a tool argument can hold
        // file contents, tokens, clipboard text).
        var payload = new
        {
            promptId,
            conversationId,
            tool = request.ToolName,
            description = request.Description,
            category = request.Category.ToString(),
            argumentSummary = SummariseArguments(request.ArgumentsJson),
            argumentDetail = ToolCardFormatter.BuildInput(request.ArgumentsJson),
            dangerClass = danger.IsDangerous ? danger.ReasonKey : "safe",
        };
        if (conversationId is not null)
            _pendingConsents[promptId] = new PendingConsent(conversationId, payload);
        _bridge!.PostConsentRequest(payload);
        _logger.LogInformation("CONSENT REQUEST posted: prompt={Prompt} tool={Tool} category={Category} danger={Danger} conv={Conv} (args not logged)",
            promptId, request.ToolName, request.Category, danger.IsDangerous ? danger.ReasonKey : "safe", conversationId ?? "(none)");
        ConsentRequestPosted?.Invoke(promptId);

        // The orchestrator awaits this inline; do NOT block a bridge callback
        // thread — this await yields until consent.respond completes the TCS.
        try
        {
            var response = await tcs.Task;
            _logger.LogInformation("CONSENT RESOLVED: prompt={Prompt} denied={Denied} once={Once} always={Always}",
                promptId, response.Denied, response.AllowOnce, response.AllowAlways);
            return response;
        }
        finally
        {
            _pendingConsents.TryRemove(promptId, out _);
        }
    }

    private void OnConsentRespond(ConsentRespondPayload payload)
    {
        if (!_consents.TryRemove(payload.PromptId, out var tcs))
        {
            _logger.LogWarning("consent.respond for an unknown/expired prompt {Prompt} — ignored", payload.PromptId);
            return;
        }
        var response = payload.Decision switch
        {
            "deny" => ToolConsentResponse.Deny,
            // allow-once / allow-session both map to the process-lifetime
            // session grant (.Once); allow-always is a future page control.
            "allow" => ToolConsentResponse.Once,
            _ => ToolConsentResponse.Deny,
        };
        tcs.TrySetResult(response);
    }

    private static string SummariseArguments(string argumentsJson)
    {
        // A short, non-secret shape summary: the argument NAMES and value
        // lengths, never the values. Enough for a card, safe for a log if it
        // were ever logged (it is not).
        try
        {
            using var doc = JsonDocument.Parse(argumentsJson);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return "(arguments)";
            var parts = doc.RootElement.EnumerateObject()
                .Select(p => $"{p.Name}: {DescribeValue(p.Value)}")
                .Take(8);
            var summary = string.Join(", ", parts);
            return summary.Length <= 300 ? summary : summary[..300] + "…";
        }
        catch (JsonException)
        {
            return "(arguments)";
        }
    }

    private static string DescribeValue(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => $"{value.GetString()!.Length} chars",
        JsonValueKind.Number => value.ToString(),
        JsonValueKind.True or JsonValueKind.False => value.GetBoolean().ToString(),
        JsonValueKind.Array => $"{value.GetArrayLength()} items",
        JsonValueKind.Object => "{…}",
        _ => "null",
    };
}
