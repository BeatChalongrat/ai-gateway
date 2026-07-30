using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Storage;

var token = Environment.GetEnvironmentVariable("SRAN_BROWSER_E2E_TOKEN")
            ?? throw new InvalidOperationException("SRAN_BROWSER_E2E_TOKEN is required.");
var database = Environment.GetEnvironmentVariable("SRAN_BROWSER_E2E_DB")
               ?? throw new InvalidOperationException("SRAN_BROWSER_E2E_DB is required.");
Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(database))!);

var credentials = new FixedCredentialStore(token);
await using var bridge = new BrowserBridgeService(
    credentials,
    NullLogger<BrowserBridgeService>.Instance,
    defaultTimeout: TimeSpan.FromSeconds(15));
bridge.Start();

Write(new
{
    @event = "ready",
    pipe_name = SranDesktop.Infrastructure.Native.Browser.BrowserWire.PipeName()
});

while (await Console.In.ReadLineAsync() is { } line)
{
    using var command = JsonDocument.Parse(line);
    var name = command.RootElement.GetProperty("command").GetString();
    if (name == "stop")
        break;
    if (name is not ("run" or "transfer" or "interrupt"))
    {
        Write(new { @event = "error", code = "UnknownHarnessCommand" });
        continue;
    }

    var value = command.RootElement.TryGetProperty("value", out var requested)
        ? requested.GetString() ?? "production-chain"
        : "production-chain";
    try
    {
        await WaitForBridgeAsync(bridge, TimeSpan.FromSeconds(30));
        var result = name switch
        {
            "transfer" => await ExecuteTransferAsync(
                bridge,
                database,
                Environment.GetEnvironmentVariable("SRAN_BROWSER_E2E_WORKSPACE")
                ?? throw new InvalidOperationException(
                    "SRAN_BROWSER_E2E_WORKSPACE is required for transfer mode.")),
            "interrupt" => await ExecuteInterruptedAsync(bridge, database, value),
            _ => await ExecuteAsync(bridge, database, value)
        };
        Write(result);
    }
    catch (Exception ex)
    {
        Write(new
        {
            @event = "result",
            success = false,
            error_code = ex.GetType().Name,
            error = ex.Message
        });
    }
}

return;

static async Task<object> ExecuteTransferAsync(
    IBrowserBridgeService bridge,
    string database,
    string workspace)
{
    var uploadPath = Path.Combine(workspace, "authorized-upload.txt");
    var downloadPath = Path.Combine(workspace, "sran-fixture.txt");
    var broker = new WorkspaceFileBroker(
        [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
        workspace);
    var conversations = new SqliteConversationStore(database);
    var tasks = new SqliteAgentTaskStore(database);
    var runtime = new DurableTaskRuntime(
        tasks,
        new GoalInterpreter(),
        new CapabilityRouter());
    var registry = new McpToolRegistry();
    registry.Register(new BrowserReadPageTool(bridge));
    registry.Register(new BrowserUploadTool(bridge, broker));
    registry.Register(new BrowserDownloadTool(bridge, broker));
    var host = new McpToolHost(registry);
    var conversationId = $"browser-transfer-{Guid.NewGuid():N}";
    var now = DateTimeOffset.UtcNow;
    await conversations.UpsertConversationAsync(new Conversation
    {
        Id = conversationId,
        Title = "Production browser transfer chain",
        ModelId = "scripted-production-chain",
        Source = ConversationSource.Local,
        CreatedAt = now,
        UpdatedAt = now
    });
    var orchestrator = new ChatOrchestrator(
        new TransferBrowserGateway(uploadPath, downloadPath),
        host,
        new HarnessPermissionService(),
        conversations,
        NullLogger<ChatOrchestrator>.Instance,
        durableRuntime: runtime);
    orchestrator.ConsentRequested += _ =>
        Task.FromResult(ToolConsentResponse.Once);

    var stream = new List<AgentStreamEvent>();
    await foreach (var item in orchestrator.RunAsync(
                       "scripted-production-chain",
                       [
                           new GatewayMessage
                           {
                               Role = "user",
                               Content =
                                   "Inspect the active browser page to find the file controls. " +
                                   "Select authorized-upload.txt for upload, then download " +
                                   "sran-fixture.txt into the authorized workspace and verify both."
                           }
                       ],
                       conversationId))
        stream.Add(item);

    var actions = stream.OfType<AgentToolResultEvent>().ToArray();
    var upload = actions.Single(item => item.ToolName == "browser_upload");
    var download = actions.Single(item => item.ToolName == "browser_download");
    var taskId = upload.UnifiedResult?.TaskId;
    if (string.IsNullOrWhiteSpace(taskId))
    {
        var diagnostic = SensitiveDataRedactor.Redact(
            string.Join(
                " | ",
                actions.Select(item =>
                {
                    var detail = item.FullResult ?? item.ResultPreview;
                    return $"{item.ToolName}:success={item.Success}:" +
                           detail[^Math.Min(500, detail.Length)..];
                })));
        throw new InvalidOperationException(
            "The transfer run did not emit a durable task id. " +
            diagnostic[..Math.Min(2_000, diagnostic.Length)]);
    }
    var snapshot = await tasks.GetAsync(taskId)
                   ?? throw new InvalidOperationException(
                       "The durable transfer run was not persisted.");
    var events = await runtime.GetEventsAsync(taskId);
    var downloaded = await broker.ReadFileAsync(
        downloadPath,
        512 * 1024,
        CancellationToken.None);
    return new
    {
        @event = "transfer_result",
        success = upload.Success && download.Success,
        upload_verified = upload.UnifiedResult?.Verified == true,
        download_verified = download.UnifiedResult?.Verified == true,
        upload_task_id = upload.UnifiedResult?.TaskId,
        download_task_id = download.UnifiedResult?.TaskId,
        task_status = snapshot.Task.Status.ToString(),
        current_step_id = snapshot.Task.CurrentStepId,
        agent_step_count = snapshot.Plan.Milestones.Sum(item => item.Steps.Count),
        outcomes = snapshot.Plan.Outcomes.Select(item => new
        {
            item.Description,
            status = item.Status.ToString(),
            item.VerificationResult
        }).ToArray(),
        steps = snapshot.Plan.Milestones.SelectMany(item => item.Steps)
            .Select(item => new
            {
                item.StepId,
                item.Description,
                status = item.Status.ToString(),
                side_effect = item.SideEffectClass.ToString(),
                item.FailureCode
            }).ToArray(),
        event_sequence = events.Select(item => item.EventType).ToArray(),
        downloaded_name = downloaded.Name,
        downloaded_length = downloaded.Bytes.LongLength,
        downloaded_sha256 = downloaded.Sha256,
        downloaded_text = System.Text.Encoding.UTF8.GetString(downloaded.Bytes)
    };
}

/// <summary>
/// Native-host crash DURING a consequential submit. The relay is killed after
/// the request frame is on the pipe, so the page may or may not have submitted
/// — the honest classification is "unknown". Proves three things end to end:
/// the run never claims success, the durable step is left needing
/// reconciliation, and an identical repeat is refused rather than double-
/// submitting once the bridge comes back.
/// </summary>
static async Task<object> ExecuteInterruptedAsync(
    IBrowserBridgeService bridge,
    string database,
    string value)
{
    var conversations = new SqliteConversationStore(database);
    var tasks = new SqliteAgentTaskStore(database);
    var runtime = new DurableTaskRuntime(
        tasks,
        new GoalInterpreter(),
        new CapabilityRouter());
    var interrupting = new RelayKillingBridge(bridge);
    var registry = new McpToolRegistry();
    registry.Register(new BrowserReadPageTool(interrupting));
    registry.Register(new BrowserTypeTool(interrupting));
    var host = new McpToolHost(registry);
    var conversationId = $"browser-interrupt-{Guid.NewGuid():N}";
    var now = DateTimeOffset.UtcNow;
    await conversations.UpsertConversationAsync(new Conversation
    {
        Id = conversationId,
        Title = "Interrupted production browser chain",
        ModelId = "scripted-production-chain",
        Source = ConversationSource.Local,
        CreatedAt = now,
        UpdatedAt = now
    });
    var orchestrator = new ChatOrchestrator(
        new SubmittingBrowserGateway(value),
        host,
        new HarnessPermissionService(),
        conversations,
        NullLogger<ChatOrchestrator>.Instance,
        durableRuntime: runtime);
    orchestrator.ConsentRequested += _ =>
        Task.FromResult(ToolConsentResponse.Once);

    var stream = new List<AgentStreamEvent>();
    await foreach (var item in orchestrator.RunAsync(
                       "scripted-production-chain",
                       [
                           new GatewayMessage
                           {
                               Role = "user",
                               Content =
                                   $"Submit the workflow form with {value} and verify it."
                           }
                       ],
                       conversationId))
        stream.Add(item);

    var submit = stream.OfType<AgentToolResultEvent>()
        .LastOrDefault(item => item.ToolName == "browser_type");
    var taskId = stream.OfType<AgentToolResultEvent>()
        .Select(item => item.UnifiedResult?.TaskId)
        .LastOrDefault(id => !string.IsNullOrWhiteSpace(id));
    var snapshot = taskId is null ? null : await tasks.GetAsync(taskId);
    var events = taskId is null
        ? []
        : (await runtime.GetEventsAsync(taskId)).Select(item => item.EventType).ToArray();
    var steps = snapshot?.Plan.Milestones.SelectMany(item => item.Steps).ToArray() ?? [];
    return new
    {
        @event = "interrupt_result",
        relay_killed = interrupting.RelayKilled,
        dispatched_mutations = interrupting.MutatingDispatches,
        submit_reported_success = submit?.Success == true,
        submit_verified = submit?.UnifiedResult?.Verified == true,
        submit_failure_code = submit?.UnifiedResult?.FailureCode,
        submit_detail = submit is null
            ? null
            : SensitiveDataRedactor.Redact(
                (submit.FullResult ?? submit.ResultPreview ?? "")
                [..Math.Min(400, (submit.FullResult ?? submit.ResultPreview ?? "").Length)]),
        task_id = taskId,
        task_status = snapshot?.Task.Status.ToString(),
        step_statuses = steps.Select(item => item.Status.ToString()).ToArray(),
        step_failure_codes = steps.Select(item => item.FailureCode).ToArray(),
        event_sequence = events
    };
}

static async Task<object> ExecuteAsync(
    IBrowserBridgeService bridge,
    string database,
    string value)
{
    var conversations = new SqliteConversationStore(database);
    var tasks = new SqliteAgentTaskStore(database);
    var runtime = new DurableTaskRuntime(
        tasks,
        new GoalInterpreter(),
        new CapabilityRouter());
    var registry = new McpToolRegistry();
    registry.Register(new BrowserReadPageTool(bridge));
    registry.Register(new BrowserTypeTool(bridge));
    var host = new McpToolHost(registry);
    var permissions = new HarnessPermissionService();
    var gateway = new AdaptiveBrowserGateway(value);
    var conversationId = $"browser-chain-{Guid.NewGuid():N}";
    var now = DateTimeOffset.UtcNow;
    await conversations.UpsertConversationAsync(new Conversation
    {
        Id = conversationId,
        Title = "Production browser chain",
        ModelId = "scripted-production-chain",
        Source = ConversationSource.Local,
        CreatedAt = now,
        UpdatedAt = now
    });
    var orchestrator = new ChatOrchestrator(
        gateway,
        host,
        permissions,
        conversations,
        NullLogger<ChatOrchestrator>.Instance,
        durableRuntime: runtime);
    orchestrator.ConsentRequested += _ =>
        Task.FromResult(ToolConsentResponse.Once);

    var stream = new List<AgentStreamEvent>();
    await foreach (var item in orchestrator.RunAsync(
                       "scripted-production-chain",
                       [
                           new GatewayMessage
                           {
                               Role = "user",
                               Content =
                                   $"Set Workflow value to {value} in the active browser tab and verify it."
                           }
                       ],
                       conversationId))
        stream.Add(item);

    var actions = stream.OfType<AgentToolResultEvent>().ToArray();
    var mutation = actions.Single(item => item.ToolName == "browser_type");
    var taskId = mutation.UnifiedResult?.TaskId;
    if (string.IsNullOrWhiteSpace(taskId))
    {
        var diagnostic = SensitiveDataRedactor.Redact(
            mutation.FullResult ?? mutation.ResultPreview);
        throw new InvalidOperationException(
            $"Durable task id was not emitted (success={mutation.Success}). " +
            diagnostic[..Math.Min(500, diagnostic.Length)]);
    }
    var snapshot = await tasks.GetAsync(taskId)
                   ?? throw new InvalidOperationException("Durable run was not persisted.");
    var events = await runtime.GetEventsAsync(taskId);
    var session = bridge.CurrentSession
                  ?? throw new InvalidOperationException("Browser session disappeared.");
    return new
    {
        @event = "result",
        success = mutation.Success,
        verified = mutation.UnifiedResult?.Verified == true,
        failure_code = mutation.UnifiedResult?.FailureCode,
        failure_reason = mutation.UnifiedResult?.FailureReason,
        verification_reason = mutation.UnifiedResult?.VerificationReason,
        expected_effect_observed = mutation.UnifiedResult?.ExpectedEffectObserved,
        target_validated = mutation.UnifiedResult?.TargetValidated,
        task_id = taskId,
        task_status = snapshot.Task.Status.ToString(),
        agent_step_count = snapshot.Plan.Milestones.Sum(item => item.Steps.Count),
        event_sequence = events.Select(item => item.EventType).ToArray(),
        bridge_session_id = session.BridgeSessionId,
        browser_instance_id = session.BrowserInstanceId,
        browser_profile_id = session.BrowserProfileId,
        value
    };
}

static async Task WaitForBridgeAsync(
    IBrowserBridgeService bridge,
    TimeSpan timeout)
{
    var deadline = DateTimeOffset.UtcNow + timeout;
    while (!bridge.IsConnected && DateTimeOffset.UtcNow < deadline)
        await Task.Delay(50);
    if (!bridge.IsConnected)
        throw new TimeoutException("Authenticated extension/native-host connection did not appear.");
}

static void Write(object value)
{
    Console.Out.WriteLine(JsonSerializer.Serialize(value));
    Console.Out.Flush();
}

file sealed class TransferBrowserGateway(
    string uploadPath,
    string downloadPath) : IGatewayApiClient
{
    private int _request;

    public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
        GatewayCompletionRequest request,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var number = Interlocked.Increment(ref _request);
        if (number is 1 or 3)
        {
            yield return ToolCall(
                $"transfer-read-{number}",
                "browser_read_page",
                """{"filter":"interactive"}""");
            yield return Stop("tool_calls");
        }
        else if (number == 2)
        {
            var observation = ReadObservation(request);
            var identity = Identity(observation);
            var target = ElementReference(observation, "Upload fixture file");
            yield return ToolCall(
                "upload-call",
                "browser_upload",
                JsonSerializer.Serialize(new
                {
                    path = uploadPath,
                    @ref = target,
                    tab_id = identity.TabId,
                    observation_id = identity.ObservationId,
                    dom_generation = identity.DomGeneration,
                    expected_state = "Upload selected authorized-upload.txt"
                }));
            yield return Stop("tool_calls");
        }
        else if (number == 4)
        {
            var observation = ReadObservation(request);
            var identity = Identity(observation);
            var target = ElementReference(observation, "Download fixture file");
            yield return ToolCall(
                "download-call",
                "browser_download",
                JsonSerializer.Serialize(new
                {
                    destination_path = downloadPath,
                    expected_file_name = "sran-fixture.txt",
                    @ref = target,
                    tab_id = identity.TabId,
                    observation_id = identity.ObservationId,
                    dom_generation = identity.DomGeneration
                }));
            yield return Stop("tool_calls");
        }
        else
        {
            yield return new SseFrame(
                null,
                """{"choices":[{"index":0,"delta":{"content":"Verified production browser transfers."},"finish_reason":null}]}""");
            yield return Stop("stop");
        }
        await Task.Yield();
    }

    public Task<GatewaySearchResponse> SearchAsync(
        string query,
        int maxResults = 5,
        CancellationToken ct = default) =>
        Task.FromResult(new GatewaySearchResponse(query, "harness", 0, []));

    private static string ReadObservation(GatewayCompletionRequest request)
    {
        var protectedObservation = request.Messages.Last(
            message => message.Role == "tool").Content?.ToString() ?? "";
        var candidate = ObservationEnvelopePrefix(protectedObservation);
        return UntrustedObservationBoundary.TryReadEnvelope(candidate, out var envelope)
            ? envelope!.Content
            : protectedObservation;
    }

    private static (string ObservationId, long DomGeneration, int TabId) Identity(
        string observation)
    {
        var identity = Regex.Match(
            observation,
            @"observation_id=([^;]+); dom_generation=(\d+); tab_id=(\d+)");
        if (!identity.Success)
        {
            var diagnostic = SensitiveDataRedactor.Redact(observation);
            throw new InvalidOperationException(
                "The transfer observation did not contain a tab-bound identity. " +
                diagnostic[..Math.Min(400, diagnostic.Length)]);
        }
        return (
            identity.Groups[1].Value,
            long.Parse(identity.Groups[2].Value),
            int.Parse(identity.Groups[3].Value));
    }

    private static int ElementReference(string observation, string name)
    {
        var target = Regex.Match(
            observation,
            $@"^\[(\d+)\].*{Regex.Escape(name)}",
            RegexOptions.Multiline);
        if (!target.Success)
            throw new InvalidOperationException(
                $"The transfer observation did not contain '{name}'.");
        return int.Parse(target.Groups[1].Value);
    }

    private static string ObservationEnvelopePrefix(string value)
    {
        if (!value.StartsWith(
                UntrustedObservationBoundary.Marker,
                StringComparison.Ordinal))
            return value;
        var jsonStart = value.IndexOf('\n');
        if (jsonStart < 0) return value;
        var jsonEnd = value.IndexOf('\n', jsonStart + 1);
        return jsonEnd < 0 ? value : value[..jsonEnd];
    }

    private static SseFrame ToolCall(
        string id,
        string name,
        string arguments) =>
        new(
            null,
            JsonSerializer.Serialize(new
            {
                choices = new[]
                {
                    new
                    {
                        index = 0,
                        delta = new
                        {
                            tool_calls = new[]
                            {
                                new
                                {
                                    index = 0,
                                    id,
                                    type = "function",
                                    function = new { name, arguments }
                                }
                            }
                        },
                        finish_reason = (string?)null
                    }
                }
            }));

    private static SseFrame Stop(string reason) =>
        new(
            null,
            $$"""{"choices":[{"index":0,"delta":{},"finish_reason":"{{reason}}"}]}""");
}

/// <summary>Kills the real native-messaging relay while the first mutating
/// request is in flight, so the desktop side loses the answer to a request the
/// page may already have executed.</summary>
file sealed class RelayKillingBridge(IBrowserBridgeService inner) : IBrowserBridgeService
{
    private static readonly HashSet<string> Mutating = new(StringComparer.Ordinal)
    {
        "page.type", "page.click", "page.navigate", "page.upload", "page.download"
    };

    private int _killed;

    public bool RelayKilled => Volatile.Read(ref _killed) == 1;
    public int MutatingDispatches { get; private set; }
    public bool IsConnected => inner.IsConnected;
    public DateTimeOffset? LastSeenAt => inner.LastSeenAt;
    public BrowserBridgeSession? CurrentSession => inner.CurrentSession;

    public event EventHandler? StatusChanged
    {
        add => inner.StatusChanged += value;
        remove => inner.StatusChanged -= value;
    }

    public void Start() => inner.Start();
    public string GetOrCreatePairingToken() => inner.GetOrCreatePairingToken();

    public Task<JsonElement> RequestAsync(
        string method,
        object? @params = null,
        CancellationToken ct = default,
        TimeSpan? timeout = null)
    {
        if (!Mutating.Contains(method))
            return inner.RequestAsync(method, @params, ct, timeout);

        MutatingDispatches++;
        var request = inner.RequestAsync(method, @params, ct, timeout);
        if (Interlocked.CompareExchange(ref _killed, 1, 0) == 0)
            _ = Task.Run(async () =>
            {
                // Let the frame reach Chrome (and very likely the page) before
                // the relay dies — that is what makes the outcome unknown
                // rather than trivially "never happened".
                await Task.Delay(60);
                foreach (var relay in
                         System.Diagnostics.Process.GetProcessesByName(
                             "SranDesktop.BrowserHost"))
                {
                    try
                    {
                        relay.Kill(entireProcessTree: true);
                    }
                    catch (Exception ex) when (
                        ex is InvalidOperationException
                            or System.ComponentModel.Win32Exception
                            or NotSupportedException)
                    {
                    }
                    finally
                    {
                        relay.Dispose();
                    }
                }
            });
        return request;
    }
}

file sealed class SubmittingBrowserGateway(string value) : IGatewayApiClient
{
    private int _request;

    public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
        GatewayCompletionRequest request,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var number = Interlocked.Increment(ref _request);
        if (number == 1)
        {
            yield return HarnessFrames.ToolCall(
                "interrupt-read",
                "browser_read_page",
                """{"filter":"interactive"}""");
            yield return HarnessFrames.Stop("tool_calls");
        }
        else if (number == 2)
        {
            var observation = HarnessFrames.ReadObservation(request);
            var identity = Regex.Match(
                observation,
                @"observation_id=([^;]+); dom_generation=(\d+); tab_id=(\d+)");
            var target = Regex.Match(
                observation,
                @"^\[(\d+)\].*Slow workflow value",
                RegexOptions.Multiline);
            if (!identity.Success || !target.Success)
                throw new InvalidOperationException(
                    "The interrupted-chain observation was not tab-bound.");
            yield return HarnessFrames.ToolCall(
                "interrupt-submit",
                "browser_type",
                JsonSerializer.Serialize(new
                {
                    @ref = int.Parse(target.Groups[1].Value),
                    text = value,
                    clear = true,
                    submit = true,
                    observation_id = identity.Groups[1].Value,
                    dom_generation = long.Parse(identity.Groups[2].Value),
                    tab_id = int.Parse(identity.Groups[3].Value),
                    expected_state = "Slow submitted once"
                }));
            yield return HarnessFrames.Stop("tool_calls");
        }
        else
        {
            yield return new SseFrame(
                null,
                """{"choices":[{"index":0,"delta":{"content":"The submit outcome is unknown."},"finish_reason":null}]}""");
            yield return HarnessFrames.Stop("stop");
        }
        await Task.Yield();
    }

    public Task<GatewaySearchResponse> SearchAsync(
        string query,
        int maxResults = 5,
        CancellationToken ct = default) =>
        Task.FromResult(new GatewaySearchResponse(query, "harness", 0, []));
}

file static class HarnessFrames
{
    public static string ReadObservation(GatewayCompletionRequest request)
    {
        var protectedObservation = request.Messages.Last(
            message => message.Role == "tool").Content?.ToString() ?? "";
        var candidate = EnvelopePrefix(protectedObservation);
        return UntrustedObservationBoundary.TryReadEnvelope(candidate, out var envelope)
            ? envelope!.Content
            : protectedObservation;
    }

    public static SseFrame ToolCall(string id, string name, string arguments) =>
        new(
            null,
            JsonSerializer.Serialize(new
            {
                choices = new[]
                {
                    new
                    {
                        index = 0,
                        delta = new
                        {
                            tool_calls = new[]
                            {
                                new
                                {
                                    index = 0,
                                    id,
                                    type = "function",
                                    function = new { name, arguments }
                                }
                            }
                        },
                        finish_reason = (string?)null
                    }
                }
            }));

    public static SseFrame Stop(string reason) =>
        new(
            null,
            $$"""{"choices":[{"index":0,"delta":{},"finish_reason":"{{reason}}"}]}""");

    private static string EnvelopePrefix(string value)
    {
        if (!value.StartsWith(
                UntrustedObservationBoundary.Marker,
                StringComparison.Ordinal))
            return value;
        var jsonStart = value.IndexOf('\n');
        if (jsonStart < 0) return value;
        var jsonEnd = value.IndexOf('\n', jsonStart + 1);
        return jsonEnd < 0 ? value : value[..jsonEnd];
    }
}

file sealed class FixedCredentialStore(string token) : ICredentialStore
{
    public void Save(string key, string value)
    {
    }

    public string? Load(string key) =>
        key == "browser:pairing-token" ? token : null;

    public void Delete(string key)
    {
    }
}

file sealed class HarnessPermissionService : IPermissionService
{
    public Task<PermissionDecision> CheckAsync(
        IMcpTool tool,
        string scope,
        CancellationToken ct = default) =>
        Task.FromResult(PermissionDecision.NeedsConsent);

    public void RecordSessionDecision(
        string toolName,
        string scope,
        PermissionLevel level)
    {
    }

    public Task PersistDecisionAsync(
        string toolName,
        string scope,
        string permission,
        CancellationToken ct = default) =>
        Task.CompletedTask;
}

file sealed class AdaptiveBrowserGateway(string value) : IGatewayApiClient
{
    private int _request;

    public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
        GatewayCompletionRequest request,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var number = Interlocked.Increment(ref _request);
        if (number == 1)
        {
            yield return ToolCall("read-call", "browser_read_page", """{"filter":"interactive"}""");
            yield return Stop("tool_calls");
        }
        else if (number == 2)
        {
            var protectedObservation = request.Messages.Last(message => message.Role == "tool")
                .Content?.ToString() ?? "";
            var envelopeCandidate = ObservationEnvelopePrefix(protectedObservation);
            var observation = UntrustedObservationBoundary.TryReadEnvelope(
                envelopeCandidate,
                out var envelope)
                ? envelope!.Content
                : protectedObservation;
            var identity = Regex.Match(
                observation,
                @"observation_id=([^;]+); dom_generation=(\d+); tab_id=(\d+)");
            var target = Regex.Match(
                observation,
                @"^\[(\d+)\].*Workflow value",
                RegexOptions.Multiline);
            if (!identity.Success)
                throw new InvalidOperationException(
                    "The production DOM observation did not contain a tab-bound identity.");
            if (!target.Success)
            {
                var diagnostic = SensitiveDataRedactor.Redact(observation);
                throw new InvalidOperationException(
                    "The production DOM observation did not contain the Workflow value target. " +
                    diagnostic[..Math.Min(300, diagnostic.Length)]);
            }
            var arguments = JsonSerializer.Serialize(new
            {
                @ref = int.Parse(target.Groups[1].Value),
                text = value,
                clear = true,
                submit = false,
                observation_id = identity.Groups[1].Value,
                dom_generation = long.Parse(identity.Groups[2].Value),
                tab_id = int.Parse(identity.Groups[3].Value)
            });
            yield return ToolCall("type-call", "browser_type", arguments);
            yield return Stop("tool_calls");
        }
        else
        {
            yield return new SseFrame(
                null,
                """{"choices":[{"index":0,"delta":{"content":"Verified production browser action."},"finish_reason":null}]}""");
            yield return Stop("stop");
        }
        await Task.Yield();
    }

    public Task<GatewaySearchResponse> SearchAsync(
        string query,
        int maxResults = 5,
        CancellationToken ct = default) =>
        Task.FromResult(new GatewaySearchResponse(query, "harness", 0, []));

    private static SseFrame ToolCall(
        string id,
        string name,
        string arguments) =>
        new(
            null,
            JsonSerializer.Serialize(new
            {
                choices = new[]
                {
                    new
                    {
                        index = 0,
                        delta = new
                        {
                            tool_calls = new[]
                            {
                                new
                                {
                                    index = 0,
                                    id,
                                    type = "function",
                                    function = new { name, arguments }
                                }
                            }
                        },
                        finish_reason = (string?)null
                    }
                }
            }));

    private static SseFrame Stop(string reason) =>
        new(
            null,
            $$"""{"choices":[{"index":0,"delta":{},"finish_reason":"{{reason}}"}]}""");

    private static string ObservationEnvelopePrefix(string value)
    {
        if (!value.StartsWith(
                UntrustedObservationBoundary.Marker,
                StringComparison.Ordinal))
            return value;
        var jsonStart = value.IndexOf('\n');
        if (jsonStart < 0) return value;
        var jsonEnd = value.IndexOf('\n', jsonStart + 1);
        return jsonEnd < 0 ? value : value[..jsonEnd];
    }
}
