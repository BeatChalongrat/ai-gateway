using System.Text.Json;
using DocumentFormat.OpenXml.Packaging;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Infrastructure.Workers;
using Xunit;
using W = DocumentFormat.OpenXml.Wordprocessing;

namespace SranDesktop.Tests.Documents;

/// <summary>
/// Documents produced by the two entry points nobody is watching: a scheduled
/// firing and a sub-agent.
///
/// The runtime is the same one a chat turn uses — that is the property being
/// tested. A scheduled job that wrote a file through some other path would have
/// no AgentStep behind it, no verification that the document reopens, and no
/// protection for the file it overwrote.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class ScheduledAndSubagentDocumentTests : IAsyncLifetime
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-doc-runs-{Guid.NewGuid():N}");
    private string _workspace = "";
    private SqliteConversationStore _conversations = null!;
    private SqliteAgentTaskStore _store = null!;
    private DurableTaskRuntime _runtime = null!;
    private DocumentWorkerService _documents = null!;
    private DocumentTool _tool = null!;

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        _workspace = Path.Combine(_root, "workspace");
        Directory.CreateDirectory(_workspace);
        var databasePath = Path.Combine(_root, "durable.db");
        _conversations = new SqliteConversationStore(databasePath);
        _store = new SqliteAgentTaskStore(databasePath);
        _runtime = new DurableTaskRuntime(_store, new GoalInterpreter(), new CapabilityRouter());
        Rebuild();
        return Task.CompletedTask;
    }

    /// <summary>Fresh worker service over the same workspace — what the app
    /// restarting looks like from the document layer's point of view.</summary>
    private void Rebuild()
    {
        _documents = new DocumentWorkerService(
            new IsolatedWorkerHost(
                new WorkerImageResolver(Path.Combine(AppContext.BaseDirectory, "workers"))),
            new WorkspaceFileBroker(
                [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWrite)],
                _workspace),
            Path.Combine(_root, "scratch"));
        _tool = new DocumentTool(_documents);
    }

    // ── Scheduler ───────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Scheduled_Document_Is_Created_Under_A_Scheduler_Triggered_Step()
    {
        var context = await RunAsync(AgentTriggerType.Scheduler);
        var output = Path.Combine(_workspace, "nightly.docx");

        var result = await ExecuteThroughRuntimeAsync(
            context, CreatePayload(output, "Nightly report"));

        result.Success.Should().BeTrue(result.Error);
        (await _store.GetAsync(context.TaskId))!.Task.TriggerType
            .Should().Be(AgentTriggerType.Scheduler);
        (await _runtime.GetEventsAsync(context.TaskId))
            .Should().Contain(item => item.EventType == "ActionDispatching");

        // The file is verified by opening it, not by its existence.
        TextOf(output).Should().Contain("Nightly report");
    }

    [Fact]
    public async Task A_Scheduled_Edit_Leaves_The_Original_Intact_When_Validation_Fails()
    {
        var target = Path.Combine(_workspace, "contract.docx");
        await SeedAsync(target, "Original clause.");
        var before = await File.ReadAllBytesAsync(target);

        var result = await ExecuteThroughRuntimeAsync(
            await RunAsync(AgentTriggerType.Scheduler),
            EditPayload(target, "Original clause.", "Amended clause.",
                // An expectation the edit cannot satisfy: validation must refuse
                // to publish rather than overwrite with something unverified.
                expect: "A CLAUSE THAT WAS NEVER WRITTEN"));

        result.Success.Should().BeFalse();
        (await File.ReadAllBytesAsync(target)).Should().Equal(before,
            "an unattended edit that failed verification must not have touched the file");
    }

    [Fact]
    public async Task A_Scheduled_Edit_That_Passes_Publishes_The_New_Content()
    {
        var target = Path.Combine(_workspace, "notes.docx");
        await SeedAsync(target, "Draft body.");

        var result = await ExecuteThroughRuntimeAsync(
            await RunAsync(AgentTriggerType.Scheduler),
            EditPayload(target, "Draft body.", "Final body.", expect: "Final body."));

        result.Success.Should().BeTrue(result.Error);
        TextOf(target).Should().Contain("Final body.").And.NotContain("Draft body.");
    }

    [Fact]
    public async Task A_Duplicate_Scheduled_Trigger_Does_Not_Accumulate_Documents()
    {
        var output = Path.Combine(_workspace, "once.docx");
        var payload = CreatePayload(output, "Weekly summary");

        var first = await ExecuteThroughRuntimeAsync(
            await RunAsync(AgentTriggerType.Scheduler), payload);
        var second = await ExecuteThroughRuntimeAsync(
            await RunAsync(AgentTriggerType.Scheduler), payload);

        first.Success.Should().BeTrue(first.Error);
        second.Success.Should().BeTrue(second.Error);
        Directory.GetFiles(_workspace, "once*.docx").Should().HaveCount(1,
            "a repeated firing rewrites the artifact; it does not accumulate copies");
    }

    [Fact]
    public async Task A_Restart_Before_Publish_Leaves_No_Half_Written_Document()
    {
        var output = Path.Combine(_workspace, "interrupted.docx");
        var context = await RunAsync(AgentTriggerType.Scheduler);
        using var cancellation = new CancellationTokenSource();

        var pending = _documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = context.TaskId,
                StepId = "step_1",
                AttemptId = $"attempt_{Guid.NewGuid():N}",
                IdempotencyKey = Guid.NewGuid().ToString("N"),
                Operation = "document.create",
                OutputPath = output,
                PayloadJson = JsonSerializer.Serialize(new
                {
                    blocks = new object[]
                    {
                        new { type = "paragraph", text = "This never lands." }
                    }
                })
            },
            cancellation.Token);
        cancellation.Cancel();
        var interrupted = await pending;

        // Cancelling after dispatch is an unknown outcome, never a clean success —
        // and whatever happened, no partial document is left where the finished
        // one belongs.
        interrupted.Verified.Should().BeFalse();
        File.Exists(output).Should().BeFalse(
            "an interrupted create must not leave a half-written file behind");

        // The app restarts and produces the document properly.
        Rebuild();
        var retry = await ExecuteThroughRuntimeAsync(
            await RunAsync(AgentTriggerType.Scheduler),
            CreatePayload(output, "Recovered report"));

        retry.Success.Should().BeTrue(retry.Error);
        TextOf(output).Should().Contain("Recovered report");
    }

    // ── Sub-agent ───────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Child_Run_Creates_A_Document_Under_Its_Own_Step()
    {
        var parent = await RunAsync(AgentTriggerType.Chat);
        var child = await ChildRunAsync(parent);
        var output = Path.Combine(_workspace, "quarterly.docx");

        var result = await ExecuteThroughRuntimeAsync(
            child, CreatePayload(output, "Quarterly summary"));

        result.Success.Should().BeTrue(result.Error);
        var snapshot = await _store.GetAsync(child.TaskId);
        snapshot!.Task.ParentRunId.Should().Be(parent.TaskId);
        snapshot.Task.TriggerType.Should().Be(AgentTriggerType.Subagent);
        (await _runtime.GetEventsAsync(child.TaskId))
            .Should().Contain(item => item.EventType == "ActionDispatching");
    }

    [Fact]
    public async Task The_Artifact_A_Child_Returns_Carries_Its_Provenance()
    {
        var parent = await RunAsync(AgentTriggerType.Chat);
        var child = await ChildRunAsync(parent);
        var output = Path.Combine(_workspace, "provenance.docx");

        var result = await _documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = child.TaskId,
                StepId = "step_1",
                AttemptId = $"attempt_{Guid.NewGuid():N}",
                IdempotencyKey = Guid.NewGuid().ToString("N"),
                Operation = "document.create",
                OutputPath = output,
                PayloadJson = JsonSerializer.Serialize(new
                {
                    blocks = new object[] { new { type = "paragraph", text = "Body." } }
                })
            },
            CancellationToken.None);

        result.Verified.Should().BeTrue(result.FailureReason);
        result.Artifact!.RunId.Should().Be(child.TaskId,
            "the parent has to be able to tell which child produced the file");
        result.Artifact.Provenance.Origin.Should().Be(ArtifactProvenance.AgentGenerated);
        result.Artifact.Provenance.RunId.Should().Be(child.TaskId);
        result.Artifact.FinalHash.Should().NotBeNullOrEmpty(
            "the parent verifies the artifact it was handed, not the claim about it");
    }

    [Fact]
    public async Task A_Child_Whose_Edit_Fails_Does_Not_Spoil_The_Document_For_Its_Sibling()
    {
        var target = Path.Combine(_workspace, "shared.docx");
        await SeedAsync(target, "Shared content.");
        var before = await File.ReadAllBytesAsync(target);
        var parent = await RunAsync(AgentTriggerType.Chat);

        var failing = await ExecuteThroughRuntimeAsync(
            await ChildRunAsync(parent),
            EditPayload(target, "Shared content.", "Broken.",
                expect: "NEVER GOING TO BE THERE"));
        var succeeding = await ExecuteThroughRuntimeAsync(
            await ChildRunAsync(parent),
            EditPayload(target, "Shared content.", "Updated content.",
                expect: "Updated content."));

        failing.Success.Should().BeFalse();
        succeeding.Success.Should().BeTrue(succeeding.Error,
            "one child's failure must not spoil the document for its sibling");
        (await File.ReadAllBytesAsync(target)).Should().NotEqual(before);
        TextOf(target).Should().Contain("Updated content.");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static object CreatePayload(string output, string title) => new
    {
        operation = "document.create",
        output_file = output,
        blocks = new object[]
        {
            new { type = "heading", level = 1, text = title },
            new { type = "paragraph", text = "Produced by an unattended run." }
        },
        expect = new { contains_text = new[] { title } }
    };

    private static object EditPayload(
        string target, string find, string replace, string expect) => new
    {
        operation = "document.edit",
        source_file = target,
        output_file = target,
        edits = new object[] { new { op = "replace_text", find, replace } },
        expect = new { contains_text = new[] { expect } }
    };

    private static string TextOf(string path)
    {
        using var document = WordprocessingDocument.Open(path, isEditable: false);
        return string.Concat(
            document.MainDocumentPart!.Document.Body!.Descendants<W.Text>()
                .Select(item => item.Text));
    }

    /// <summary>
    /// Runs the document tool the way the orchestrator does: prepare the action,
    /// execute inside the run scope, commit the result. That scope is what makes
    /// the operation a step rather than a side effect — the tool itself refuses a
    /// mutation without one.
    /// </summary>
    private async Task<ToolResult> ExecuteThroughRuntimeAsync(
        DurableTurnContext context, object payload)
    {
        var argumentsJson = JsonSerializer.Serialize(payload);
        var toolCallId = $"call_{Guid.NewGuid():N}";
        var preparation = await _runtime.PrepareActionAsync(
            context, toolCallId, "document", ToolCategory.FileSystem, argumentsJson);
        if (!preparation.Proceed)
            return ToolResult.Fail($"{preparation.FailureCode}: {preparation.FailureReason}");

        using var scope = AgentRunExecutionContext.Push(context.TaskId, toolCallId);
        using var arguments = JsonDocument.Parse(argumentsJson);
        var result = await _tool.ExecuteAsync(arguments.RootElement, CancellationToken.None);
        await _runtime.RecordResultAsync(context, preparation, result, 1);
        return result;
    }

    private async Task SeedAsync(string path, string body)
    {
        var result = await _documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = $"seed_{Guid.NewGuid():N}",
                StepId = "seed",
                AttemptId = $"attempt_{Guid.NewGuid():N}",
                IdempotencyKey = Guid.NewGuid().ToString("N"),
                Operation = "document.create",
                OutputPath = path,
                PayloadJson = JsonSerializer.Serialize(new
                {
                    blocks = new object[] { new { type = "paragraph", text = body } }
                })
            },
            CancellationToken.None);
        result.Verified.Should().BeTrue(result.FailureReason);
    }

    private async Task<DurableTurnContext> RunAsync(AgentTriggerType trigger)
    {
        var conversationId = await NewConversationAsync(trigger.ToString().ToLowerInvariant());
        return await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            trigger,
            "write the report document to the workspace",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            OperationIsFullySpecified: true));
    }

    private async Task<DurableTurnContext> ChildRunAsync(DurableTurnContext parent)
    {
        var conversationId = await NewConversationAsync("child");
        return await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Subagent,
            "write the report document to the workspace",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            ParentRunId: parent.TaskId,
            RecursionDepth: 1,
            OperationIsFullySpecified: true));
    }

    private async Task<string> NewConversationAsync(string prefix)
    {
        var conversationId = $"{prefix}-{Guid.NewGuid():N}";
        await _conversations.UpsertConversationAsync(new Conversation
        {
            Id = conversationId,
            Title = prefix,
            ModelId = "none",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        return conversationId;
    }

    public async Task DisposeAsync() =>
        await Mcp.ProductionConnectorHost.DeletePatientlyAsync(_root);
}
