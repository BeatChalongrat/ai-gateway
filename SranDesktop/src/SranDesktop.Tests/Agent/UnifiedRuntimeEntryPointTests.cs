using System.Runtime.CompilerServices;
using System.Diagnostics;
using System.Drawing;
using System.Text.RegularExpressions;
using System.Text;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
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
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Perception;
using SranDesktop.Infrastructure.Native.Uia;
using SranDesktop.ReliabilityFixture;
using Xunit;

namespace SranDesktop.Tests.Agent;

[Collection("PathGuardGrants")]
public sealed class UnifiedRuntimeEntryPointTests : IDisposable
{
    private readonly string _directory =
        Path.Combine(Path.GetTempPath(), $"sran-entry-points-{Guid.NewGuid():N}");
    private readonly SqliteConversationStore _conversations;
    private readonly SqliteAgentTaskStore _tasks;
    private readonly DurableTaskRuntime _runtime;

    public UnifiedRuntimeEntryPointTests()
    {
        var dataPath = Path.Combine(_directory, "data.db");
        _conversations = new SqliteConversationStore(dataPath);
        _tasks = new SqliteAgentTaskStore(dataPath);
        _runtime = new DurableTaskRuntime(
            _tasks,
            new GoalInterpreter(),
            new CapabilityRouter());
    }

    [Fact]
    public async Task Scheduler_Firing_Creates_And_Completes_A_Durable_Run()
    {
        var scheduledStore = new SqliteScheduledTaskStore(
            Path.Combine(_directory, "tasks.db"));
        var gateway = new FinalGateway("scheduled result");
        var host = EmptyToolHost();
        var permissions = AllowPermissions();
        var settings = new FixedSettings();
        var orchestrator = new ChatOrchestrator(
            gateway,
            host,
            permissions,
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        var scheduler = new TaskSchedulerService(
            scheduledStore,
            _conversations,
            orchestrator,
            _runtime,
            settings,
            NullLogger<TaskSchedulerService>.Instance);
        var task = new ScheduledTask
        {
            Id = $"schedule-{Guid.NewGuid():N}",
            Name = "Runtime integration",
            Prompt = "What is two plus two?",
            CronExpression = "0 6 * * *"
        };

        await scheduler.RunNowAsync(task);

        task.LastRunId.Should().NotBeNullOrWhiteSpace();
        var snapshot = (await _tasks.GetAsync(task.LastRunId!))!;
        snapshot.Task.TriggerType.Should().Be(AgentTriggerType.Scheduler);
        snapshot.Task.Status.Should().Be(AgentTaskStatus.Completed);
        snapshot.Task.FinalResultReference.Should().StartWith("sha256:");
        (await _runtime.GetEventsAsync(snapshot.Task.TaskId))
            .Select(item => item.EventType)
            .Should().ContainInOrder("RunCreated", "RunCompleted");
        (await scheduledStore.GetAllAsync()).Single(item => item.Id == task.Id)
            .LastRunId.Should().Be(task.LastRunId);
    }

    [Fact]
    public async Task SpawnAgent_Creates_A_Durable_Child_Run_And_Preserves_Trust()
    {
        var parentConversation = $"parent-{Guid.NewGuid():N}";
        await CreateConversationAsync(parentConversation);
        var parent = await _runtime.CreateRunAsync(new DurableRunRequest(
            parentConversation,
            AgentTriggerType.Chat,
            "delegate a workspace summary",
            $"conversation:{parentConversation}",
            SecurityContextJson: """{"workspace":"authorized"}"""));
        var gateway = new FinalGateway("facts from an external document");
        var registry = new McpToolRegistry();
        var host = new McpToolHost(registry);
        var settings = new FixedSettings();
        var permissions = AllowPermissions();
        var parentOrchestrator = new ChatOrchestrator(
            gateway,
            host,
            permissions,
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        var tool = new SpawnAgentTool(
            gateway,
            permissions,
            _conversations,
            () => host,
            () => parentOrchestrator,
            settings,
            NullLoggerFactory.Instance,
            durableRuntime: _runtime);

        ToolResult result;
        using (AgentRunExecutionContext.Push(parent.TaskId))
        {
            result = await tool.ExecuteAsync(
                JsonDocument.Parse("""{"task":"What is two plus two?"}""")
                    .RootElement.Clone(),
                CancellationToken.None);
        }

        result.Success.Should().BeTrue();
        (await _tasks.CountChildrenAsync(parent.TaskId)).Should().Be(1);
        var childEvent = (await _runtime.GetEventsAsync(parent.TaskId))
            .Single(item => item.EventType == "ChildRunCreated");
        using var childData = JsonDocument.Parse(childEvent.DataJson);
        var childId = childData.RootElement.GetProperty("child_run_id").GetString()!;
        var child = (await _tasks.GetAsync(childId))!;
        child.Task.ParentRunId.Should().Be(parent.TaskId);
        child.Task.TriggerType.Should().Be(AgentTriggerType.Subagent);
        child.Task.SecurityContextJson.Should().Be(
            (await _tasks.GetAsync(parent.TaskId))!.Task.SecurityContextJson);
        child.Task.Status.Should().Be(AgentTaskStatus.Completed);
        UntrustedObservationBoundary.ProtectIfUntrusted(
                SpawnAgentTool.ToolName,
                result.Content)
            .Should().StartWith(UntrustedObservationBoundary.Marker);
    }

    [Fact]
    public async Task Filesystem_Request_Writes_Reopens_And_Verifies_Through_Runtime()
    {
        var workspace = Path.Combine(_directory, "workspace");
        Directory.CreateDirectory(workspace);
        var path = Path.Combine(workspace, "verified.txt");
        var broker = new WorkspaceFileBroker(
            [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
            workspace);
        var write = new WriteFileTool(broker);
        var registry = new McpToolRegistry();
        registry.Register(write);
        var host = new McpToolHost(registry);
        var gateway = new ScriptedToolGateway(
            write.Name,
            JsonSerializer.Serialize(new { path, content = "durable-content" }),
            "The verified file was created.");
        var permissions = AllowPermissions(PermissionDecision.NeedsConsent);
        var conversationId = $"filesystem-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host,
            permissions,
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Once);
        var events = new List<AgentStreamEvent>();

        await foreach (var item in orchestrator.RunAsync(
                           "test-model",
                           [new GatewayMessage
                           {
                               Role = "user",
                               Content =
                                   $"Create the file \"{path}\" containing exactly \"durable-content\""
                           }],
                           conversationId))
            events.Add(item);

        var result = events.OfType<AgentToolResultEvent>().Single();
        result.Success.Should().BeTrue(
            result.FullResult ?? result.ResultPreview);
        result.UnifiedResult!.Verified.Should().BeTrue(
            $"{result.UnifiedResult.FailureCode}: {result.UnifiedResult.FailureReason}");
        events.OfType<AgentDurableEventsEvent>().Should().NotBeEmpty();
        var reopened = await broker.ReadFileAsync(path, 1024, CancellationToken.None);
        Encoding.UTF8.GetString(reopened.Bytes).Should().Be("durable-content");
        (await _tasks.GetAsync(result.UnifiedResult.TaskId))!.Task.Status
            .Should().Be(AgentTaskStatus.Completed);
        (await _runtime.GetEventsAsync(result.UnifiedResult.TaskId))
            .Select(item => item.EventType)
            .Should().ContainInOrder(
                "ApprovalGranted",
                "ActionDispatching",
                "VerificationCompleted",
                "RunCompleted");
    }

    [Fact]
    public async Task Computer_State_Request_Observes_And_Verifies_Through_Runtime()
    {
        var tool = new GetComputerStateTool();
        var registry = new McpToolRegistry();
        registry.Register(tool);
        var host = new McpToolHost(registry);
        var gateway = new ScriptedToolGateway(
            tool.Name,
            "{}",
            "Computer state was inspected.");
        var permissions = AllowPermissions(PermissionDecision.NeedsConsent);
        var conversationId = $"computer-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host,
            permissions,
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Once);
        var events = new List<AgentStreamEvent>();

        await foreach (var item in orchestrator.RunAsync(
                           "test-model",
                           [new GatewayMessage
                           {
                               Role = "user",
                               Content = "Inspect the current computer state and open windows"
                           }],
                           conversationId))
            events.Add(item);

        var result = events.OfType<AgentToolResultEvent>().Single();
        result.Success.Should().BeTrue();
        result.UnifiedResult!.Verified.Should().BeTrue(
            $"{result.UnifiedResult.FailureCode}: {result.UnifiedResult.FailureReason}");
        result.FullResult.Should().Contain("windows");
        (await _runtime.GetEventsAsync(result.UnifiedResult.TaskId))
            .Should().Contain(item => item.EventType == "VerificationCompleted");
    }

    [Fact]
    public async Task Browser_Click_Uses_Pre_Post_Observation_And_Verifies_Through_Runtime()
    {
        var bridge = new StatefulBrowserBridge();
        var tool = new BrowserClickTool(bridge);
        var registry = new McpToolRegistry();
        registry.Register(tool);
        var host = new McpToolHost(registry);
        var gateway = new ScriptedToolGateway(
            tool.Name,
            JsonSerializer.Serialize(new
            {
                @ref = 7,
                tab_id = 11,
                observation_id = "dom-observation-1",
                dom_generation = 3,
                target_description = "Continue",
                role = "button",
                expected_state = "Next panel visible"
            }),
            "The verified browser action completed.");
        var conversationId = $"browser-action-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host,
            AllowPermissions(PermissionDecision.NeedsConsent),
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Once);
        var events = new List<AgentStreamEvent>();

        await foreach (var item in orchestrator.RunAsync(
                           "test-model",
                           [new GatewayMessage
                           {
                               Role = "user",
                               Content =
                                   "Click Continue in the browser to show Next panel visible."
                           }],
                           conversationId))
            events.Add(item);

        var result = events.OfType<AgentToolResultEvent>().Single();
        result.Success.Should().BeTrue(result.FullResult);
        result.UnifiedResult!.Verified.Should().BeTrue(
            $"{result.UnifiedResult.FailureCode}: {result.UnifiedResult.FailureReason}");
        result.FullResult.Should().Contain("pre_observation_id=");
        result.FullResult.Should().Contain("post_observation_id=");
        bridge.Methods.Should().ContainInOrder(
            "page.observe",
            "page.click",
            "page.observe");
        (await _runtime.GetEventsAsync(result.UnifiedResult.TaskId))
            .Select(item => item.EventType)
            .Should().ContainInOrder(
                "ActionDispatching",
                "VerificationCompleted",
                "RunCompleted");
    }

    [Fact]
    [Trait("Category", "WindowsDesktopIntegration")]
    public async Task Mutable_Uia_Click_And_Type_Are_Verified_Through_Durable_Runtime()
    {
        using var fixture = StartReliabilityFixture();
        await WaitForFixtureWindowAsync();
        var snapshots = new UiaSnapshotStore();
        var read = new ReadScreenElementsTool(snapshots);

        var clickObservation = await read.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);
        clickObservation.Success.Should().BeTrue(clickObservation.Error);
        var clickSnapshotId = EvidenceValue(
            clickObservation.Effect!.Evidence,
            "uia_snapshot_id");
        var clickElementId = ElementId(
            clickObservation.Content,
            "Activate fixture button");
        var click = new UiaClickTool(snapshots);
        var clickResult = await ExecuteDesktopActionThroughRuntimeAsync(
            click,
            new
            {
                element_id = clickElementId,
                snapshot_id = clickSnapshotId,
                application = FixtureIdentity.WindowTitle,
                expected_state = "Button activated"
            },
            "Activate the fixture button and verify Button activated.");
        var clickVerificationObservation = await read.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);

        clickResult.Success.Should().BeTrue(
            $"{clickResult.FullResult}\nFresh UIA observation:\n" +
            clickVerificationObservation.Content);
        clickResult.FullResult.Should().Contain(ChatOrchestrator.ToolEffectMarker);
        clickResult.UnifiedResult!.Verified.Should().BeTrue();

        var typeObservation = await read.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);
        typeObservation.Success.Should().BeTrue(typeObservation.Error);
        var typeSnapshotId = EvidenceValue(
            typeObservation.Effect!.Evidence,
            "uia_snapshot_id");
        var inputElementId = ElementId(typeObservation.Content, "Fixture input");
        var type = new UiaSetValueTool(snapshots);
        var typeResult = await ExecuteDesktopActionThroughRuntimeAsync(
            type,
            new
            {
                element_id = inputElementId,
                snapshot_id = typeSnapshotId,
                application = FixtureIdentity.WindowTitle,
                text = "phase-three-value"
            },
            "Set Fixture input to phase-three-value and verify the exact value.");

        typeResult.Success.Should().BeTrue(typeResult.FullResult);
        typeResult.UnifiedResult!.Verified.Should().BeTrue();

        var verifyObservation = await read.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);
        verifyObservation.Effect!.Evidence
            .Should().Contain("observed_value=phase-three-value");
    }

    [Fact]
    [Trait("Category", "WindowsDesktopIntegration")]
    public async Task Windows_Ocr_Recognizes_A_Real_Bitmap()
    {
        using var bitmap = new Bitmap(920, 180);
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.Clear(Color.White);
            using var font = new Font("Segoe UI", 42, FontStyle.Bold);
            graphics.DrawString(
                "SRAN OCR TARGET",
                font,
                Brushes.Black,
                new PointF(18, 40));
        }

        var observation = await new WindowsOcrService().RecognizeAsync(
            bitmap,
            "test-capture",
            1,
            CancellationToken.None);

        string.Join(" ", observation.Regions.Select(item => item.Text))
            .Should().ContainAll("SRAN", "OCR", "TARGET");
        observation.Regions.Should().OnlyContain(item =>
            item.Width > 0
            && item.Height > 0
            && item.GroundingConfidence >= 0.85);
    }

    [Fact]
    [Trait("Category", "WindowsDesktopIntegration")]
    public async Task Ocr_Grounded_Click_Uses_Native_Input_And_Durable_Verification()
    {
        using var fixture = StartReliabilityFixture();
        await WaitForFixtureWindowAsync();
        var screen = new ScreenContext();
        var snapshots = new OcrSnapshotStore();
        var ocr = new ComputerOcrTool(
            screen,
            new WindowsOcrService(),
            snapshots,
            new ScreenCaptureSecurityPolicy(),
            new SolidScreenImageRedactor(),
            new ScreenCaptureIndicator());

        var observation = await ocr.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);

        observation.Success.Should().BeTrue(observation.Error);
        var observationId = EvidenceValue(
            observation.Effect!.Evidence,
            "ocr_observation_id");
        var referenceMatch = Regex.Match(
            observation.Content,
            "^\\[(\\d+)\\] \"OCR ACTION\"",
            RegexOptions.Multiline | RegexOptions.IgnoreCase);
        referenceMatch.Success.Should().BeTrue(
            $"Windows OCR should ground the fixture's OCR-only target:\n{observation.Content}");
        var click = new ComputerClickTool(
            screen,
            targets: new WindowsTargetAcquisitionRuntime(),
            ocrSnapshots: snapshots);

        var result = await ExecuteDesktopActionThroughRuntimeAsync(
            click,
            new
            {
                ocr_observation_id = observationId,
                ocr_ref = int.Parse(referenceMatch.Groups[1].Value),
                application = FixtureIdentity.WindowTitle,
                target_description = "OCR ACTION",
                desired_state = "OCR target activated"
            },
            "Click OCR ACTION and verify OCR target activated.");

        result.Success.Should().BeTrue(result.FullResult);
        result.UnifiedResult!.Verified.Should().BeTrue(
            $"{result.UnifiedResult.FailureCode}: {result.UnifiedResult.FailureReason}");
        result.FullResult.Should().Contain("grounding=windows_ocr");
        result.FullResult.Should().Contain("pre_observation_id=");
        result.FullResult.Should().Contain("post_observation_id=");
    }

    [Fact]
    [Trait("Category", "WindowsDesktopIntegration")]
    public async Task Visual_Grounded_Click_Matches_Fresh_Pixels_And_Verifies_Effect()
    {
        using var fixture = StartReliabilityFixture();
        await WaitForFixtureWindowAsync();
        var screen = new ScreenContext();
        using var visualSnapshots = new VisualSnapshotStore();
        var screenshot = new ComputerScreenshotTool(
            screen,
            visualSnapshots: visualSnapshots);
        var captured = await screenshot.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);

        captured.Success.Should().BeTrue(captured.Error);
        captured.ImageDataUri.Should().StartWith("data:image/png;base64,");
        var captureId = ScreenshotCaptureId(captured);
        captureId.Should().NotBeNullOrWhiteSpace();
        var box = VisualTargetBounds(captured);
        var click = new ComputerClickTool(
            screen,
            targets: new WindowsTargetAcquisitionRuntime(),
            visualSnapshots: visualSnapshots);

        var uiaSnapshots = new UiaSnapshotStore();
        var read = new ReadScreenElementsTool(uiaSnapshots);
        var moveObservation = await read.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);
        var moveResult = await ExecuteDesktopActionThroughRuntimeAsync(
            new UiaClickTool(uiaSnapshots),
            new
            {
                element_id = ElementId(
                    moveObservation.Content,
                    "Relocate magenta fixture surface"),
                snapshot_id = EvidenceValue(
                    moveObservation.Effect!.Evidence,
                    "uia_snapshot_id"),
                application = FixtureIdentity.WindowTitle,
                expected_state = "Visual target moved"
            },
            "Click Relocate magenta fixture surface and verify Visual target moved.");
        moveResult.Success.Should().BeTrue(moveResult.FullResult);

        var staleResult = await ExecuteDesktopActionThroughRuntimeAsync(
            click,
            VisualClickArguments(captureId, box),
            "Click the old visual target reference.");
        staleResult.Success.Should().BeFalse();
        staleResult.FullResult.Should().Contain("StaleVisualReference");
        staleResult.FullResult.Should().Contain("\"ExecutionAttempted\":false");

        var freshCapture = await screenshot.ExecuteAsync(
            JsonSerializer.SerializeToElement(new
            {
                window_title = FixtureIdentity.WindowTitle
            }),
            CancellationToken.None);
        freshCapture.Success.Should().BeTrue(freshCapture.Error);
        var freshCaptureId = ScreenshotCaptureId(freshCapture);
        var freshBox = VisualTargetBounds(freshCapture);
        var result = await ExecuteDesktopActionThroughRuntimeAsync(
            click,
            VisualClickArguments(freshCaptureId, freshBox),
            "Click the unique visual target and verify Visual target activated.");

        result.Success.Should().BeTrue(result.FullResult);
        result.UnifiedResult!.Verified.Should().BeTrue(
            $"{result.UnifiedResult.FailureCode}: {result.UnifiedResult.FailureReason}");
        result.FullResult.Should().Contain("visual_fingerprint=");
        result.FullResult.Should().Contain("visual_snapshot_persisted=false");
        result.FullResult.Should().Contain("post_observation_id=");
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task ReadOnly_Shell_Request_Uses_AppContainer_Through_Runtime()
    {
        var workspace = Path.Combine(_directory, "shell-workspace");
        Directory.CreateDirectory(workspace);
        var broker = new WorkspaceFileBroker(
            [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
            workspace);
        PathGuard.ConfigureGrantedRoots([workspace]);
        PathGuard.ConfigureWorkingDirectory(workspace);
        var tool = new RunCommandTool(
            NullLogger<RunCommandTool>.Instance,
            new FixedSettings(),
            new ShellSession(),
            new WindowsAppContainerProcessRunner(broker));
        var registry = new McpToolRegistry();
        registry.Register(tool);
        var host = new McpToolHost(registry);
        var gateway = new ScriptedToolGateway(
            tool.Name,
            JsonSerializer.Serialize(new
            {
                command = "echo sandbox-runtime",
                working_directory = workspace,
                timeout_seconds = 10,
                expected_exit_codes = new[] { 0 }
            }),
            "The sandbox inspection completed.");
        var permissions = AllowPermissions(PermissionDecision.NeedsConsent);
        var conversationId = $"shell-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host,
            permissions,
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            shellSession: new ShellSession(),
            durableRuntime: _runtime);
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Once);
        var events = new List<AgentStreamEvent>();

        await foreach (var item in orchestrator.RunAsync(
                           "test-model",
                           [new GatewayMessage
                           {
                               Role = "user",
                               Content = "Run the read-only command echo sandbox-runtime"
                           }],
                           conversationId))
            events.Add(item);

        var result = events.OfType<AgentToolResultEvent>().Single();
        result.Success.Should().BeTrue();
        result.FullResult.Should().Contain("sandbox-runtime");
        result.FullResult.Should().Contain("[exit code 0]");
        result.UnifiedResult!.Verified.Should().BeTrue();
        (await _runtime.GetEventsAsync(result.UnifiedResult.TaskId))
            .Should().Contain(item => item.EventType == "VerificationCompleted");
    }

    private static IMcpToolHost EmptyToolHost()
    {
        var host = new Mock<IMcpToolHost>();
        host.Setup(item => item.GetToolDefinitions())
            .Returns(Array.Empty<GatewayToolDefinition>());
        return host.Object;
    }

    private static IPermissionService AllowPermissions(
        PermissionDecision decision = PermissionDecision.Auto)
    {
        var permissions = new Mock<IPermissionService>();
        permissions.Setup(item => item.CheckAsync(
                It.IsAny<IMcpTool>(),
                It.IsAny<string>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(decision);
        return permissions.Object;
    }

    private async Task<AgentToolResultEvent> ExecuteDesktopActionThroughRuntimeAsync(
        IMcpTool tool,
        object arguments,
        string userIntent)
    {
        var registry = new McpToolRegistry();
        registry.Register(tool);
        var host = new McpToolHost(registry);
        var gateway = new ScriptedToolGateway(
            tool.Name,
            JsonSerializer.Serialize(arguments),
            "The verified desktop action completed.");
        var conversationId = $"computer-action-{Guid.NewGuid():N}";
        await CreateConversationAsync(conversationId);
        var orchestrator = new ChatOrchestrator(
            gateway,
            host,
            AllowPermissions(PermissionDecision.NeedsConsent),
            _conversations,
            NullLogger<ChatOrchestrator>.Instance,
            durableRuntime: _runtime);
        orchestrator.ConsentRequested += _ =>
            Task.FromResult(ToolConsentResponse.Once);
        var events = new List<AgentStreamEvent>();
        await foreach (var item in orchestrator.RunAsync(
                           "test-model",
                           [new GatewayMessage { Role = "user", Content = userIntent }],
                           conversationId))
            events.Add(item);

        return events.OfType<AgentToolResultEvent>().Single();
    }

    private static ReliabilityFixtureProcess StartReliabilityFixture()
    {
        var assembly = typeof(FixtureIdentity).Assembly.Location;
        var start = new ProcessStartInfo("dotnet")
        {
            UseShellExecute = false,
            CreateNoWindow = true
        };
        start.ArgumentList.Add(assembly);
        return new ReliabilityFixtureProcess(
            Process.Start(start)
            ?? throw new InvalidOperationException("Could not start reliability fixture."));
    }

    private static async Task WaitForFixtureWindowAsync()
    {
        var deadline = DateTimeOffset.UtcNow + TimeSpan.FromSeconds(10);
        do
        {
            if (System.Windows.Automation.AutomationElement.RootElement.FindFirst(
                    System.Windows.Automation.TreeScope.Children,
                    new System.Windows.Automation.PropertyCondition(
                        System.Windows.Automation.AutomationElement.NameProperty,
                        FixtureIdentity.WindowTitle)) is not null)
                return;
            await Task.Delay(100);
        } while (DateTimeOffset.UtcNow < deadline);

        throw new TimeoutException("Reliability fixture window did not appear.");
    }

    private static string EvidenceValue(
        IReadOnlyList<string> evidence,
        string key) =>
        evidence.Single(item => item.StartsWith($"{key}=", StringComparison.Ordinal))
            [(key.Length + 1)..];

    private static int ElementId(string listing, string accessibleName)
    {
        var match = Regex.Match(
            listing,
            $@"^\[(\d+)\].*""{Regex.Escape(accessibleName)}""",
            RegexOptions.Multiline);
        match.Success.Should().BeTrue(
            $"UIA listing should contain '{accessibleName}':\n{listing}");
        return int.Parse(match.Groups[1].Value);
    }

    private static ImageRect? FindColorBounds(Bitmap image, Color target)
    {
        var left = image.Width;
        var top = image.Height;
        var right = -1;
        var bottom = -1;
        for (var y = 0; y < image.Height; y++)
        for (var x = 0; x < image.Width; x++)
        {
            var pixel = image.GetPixel(x, y);
            if (Math.Abs(pixel.R - target.R) > 10
                || Math.Abs(pixel.G - target.G) > 10
                || Math.Abs(pixel.B - target.B) > 10)
                continue;
            left = Math.Min(left, x);
            top = Math.Min(top, y);
            right = Math.Max(right, x);
            bottom = Math.Max(bottom, y);
        }
        return right < left || bottom < top
            ? null
            : new ImageRect(left, top, right - left + 1, bottom - top + 1);
    }

    private static string ScreenshotCaptureId(ToolResult result) =>
        Regex.Match(
            result.Content,
            @"capture_id=([a-f0-9]+)",
            RegexOptions.IgnoreCase).Groups[1].Value;

    private static ImageRect VisualTargetBounds(ToolResult result)
    {
        var imageBytes = Convert.FromBase64String(
            result.ImageDataUri!["data:image/png;base64,".Length..]);
        using var stream = new MemoryStream(imageBytes);
        using var bitmap = new Bitmap(stream);
        var bounds = FindColorBounds(bitmap, Color.FromArgb(207, 45, 240));
        bounds.Should().NotBeNull(
            "the target must be located from pixels in the real window capture");
        return bounds!;
    }

    private static object VisualClickArguments(string captureId, ImageRect box) =>
        new
        {
            capture_id = captureId,
            application = FixtureIdentity.WindowTitle,
            target_description = "visual target",
            desired_state = "Visual target activated",
            vision_target = new
            {
                label = "visual target",
                role = "visual",
                x = box.X,
                y = box.Y,
                width = box.Width,
                height = box.Height,
                confidence = 0.99,
                visual_state = "magenta marker with white circle",
                parent_context = "fixture scroll area",
                neighbor_context = "below OCR target",
                alternative_candidates = Array.Empty<string>(),
                ambiguity = 0,
                expected_action = "click"
            }
        };

    private sealed class ReliabilityFixtureProcess(Process process) : IDisposable
    {
        public void Dispose()
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
            }
            process.Dispose();
        }
    }

    private async Task CreateConversationAsync(string conversationId)
    {
        var now = DateTimeOffset.UtcNow;
        await _conversations.UpsertConversationAsync(new Conversation
        {
            Id = conversationId,
            Title = "Unified runtime entry point",
            ModelId = "test-model",
            Source = ConversationSource.Local,
            CreatedAt = now,
            UpdatedAt = now
        });
    }

    private sealed class FixedSettings : ISettingsService
    {
        public UserSettings Current { get; } = new()
        {
            DefaultModel = "test-model",
            ApprovalMode = "auto"
        };
        public bool IsFirstRun => false;
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings settings) =>
            SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private sealed class FinalGateway(string content) : IGatewayApiClient
    {
        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            yield return new SseFrame(
                null,
                "{\"choices\":[{\"index\":0,\"delta\":{\"content\":" +
                JsonSerializer.Serialize(content) +
                "},\"finish_reason\":null}]}");
            yield return new SseFrame(
                null,
                """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""");
            await Task.Yield();
        }

        public Task<GatewaySearchResponse> SearchAsync(
            string query,
            int maxResults = 5,
            CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(
                query,
                "test",
                0,
                []));
    }

    private sealed class ScriptedToolGateway(
        string toolName,
        string arguments,
        string finalContent) : IGatewayApiClient
    {
        private int _request;

        public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
            GatewayCompletionRequest request,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            if (Interlocked.Increment(ref _request) == 1)
            {
                var frame = JsonSerializer.Serialize(new
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
                                        id = "call-1",
                                        type = "function",
                                        function = new
                                        {
                                            name = toolName,
                                            arguments
                                        }
                                    }
                                }
                            },
                            finish_reason = (string?)null
                        }
                    }
                });
                yield return new SseFrame(null, frame);
                yield return new SseFrame(
                    null,
                    """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""");
            }
            else
            {
                yield return new SseFrame(
                    null,
                    "{\"choices\":[{\"index\":0,\"delta\":{\"content\":" +
                    JsonSerializer.Serialize(finalContent) +
                    "},\"finish_reason\":null}]}");
                yield return new SseFrame(
                    null,
                    """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""");
            }
            await Task.Yield();
        }

        public Task<GatewaySearchResponse> SearchAsync(
            string query,
            int maxResults = 5,
            CancellationToken ct = default) =>
            Task.FromResult(new GatewaySearchResponse(query, "test", 0, []));
    }

    private sealed class StatefulBrowserBridge : IBrowserBridgeService
    {
        private int _observations;

        public List<string> Methods { get; } = [];
        public bool IsConnected => true;
        public DateTimeOffset? LastSeenAt => DateTimeOffset.UtcNow;
        public event EventHandler? StatusChanged;
        public void Start() => StatusChanged?.Invoke(this, EventArgs.Empty);
        public string GetOrCreatePairingToken() => "test-token";

        public Task<JsonElement> RequestAsync(
            string method,
            object? @params = null,
            CancellationToken ct = default,
            TimeSpan? timeout = null)
        {
            Methods.Add(method);
            if (method == "page.click")
                return Task.FromResult(JsonSerializer.SerializeToElement(new
                {
                    target_activated = true,
                    effect_observed = true,
                    expected_state_observed = true,
                    navigated = false,
                    title = "Reliability fixture",
                    url = "http://fixture.invalid/form"
                }));
            if (method != "page.observe")
                throw new InvalidOperationException($"Unexpected browser method: {method}");

            var version = Interlocked.Increment(ref _observations);
            return Task.FromResult(JsonSerializer.SerializeToElement(new
            {
                browser_instance_id = "browser-instance-1",
                browser_profile_id = "profile-test-1",
                extension_session_id = "extension-test-1",
                bridge_session_id = "bridge-test-1",
                tab_id = 11,
                frame_path = "top",
                navigation_id = 2,
                dom_generation = version == 1 ? 3 : 4,
                url = "http://fixture.invalid/form",
                title = "Reliability fixture",
                ready_state = "complete",
                viewport_width = 1280,
                viewport_height = 720,
                zoom = 1,
                active_element_fingerprint = version == 1 ? "body" : "button:submit",
                state_fingerprint = version == 1 ? "state-before" : "state-after",
                expected_state_observed = version > 1
            }));
        }
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots([]);
        PathGuard.ConfigureWorkingDirectory(null);
        if (Directory.Exists(_directory))
            Directory.Delete(_directory, recursive: true);
    }
}
