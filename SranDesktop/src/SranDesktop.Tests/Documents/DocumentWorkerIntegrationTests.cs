using System.Text.Json;
using ClosedXML.Excel;
using DocumentFormat.OpenXml.Packaging;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Workers;
using Xunit;
using W = DocumentFormat.OpenXml.Wordprocessing;

namespace SranDesktop.Tests.Documents;

/// <summary>
/// Real isolated-worker integration: a separate sandboxed process produces the
/// document, and the host publishes only after reopening it. Nothing here uses a
/// stub worker — that is the point of the phase.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class DocumentWorkerIntegrationTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-doc-worker-{Guid.NewGuid():N}");
    private readonly string _workspace;
    private readonly WorkspaceFileBroker _broker;
    private readonly DocumentWorkerService _documents;

    public DocumentWorkerIntegrationTests()
    {
        _workspace = Path.Combine(_root, "workspace");
        Directory.CreateDirectory(_workspace);
        _broker = new WorkspaceFileBroker(
            [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWrite)],
            _workspace);
        _documents = new DocumentWorkerService(
            new IsolatedWorkerHost(
                new WorkerImageResolver(
                    Path.Combine(AppContext.BaseDirectory, "workers"))),
            _broker,
            Path.Combine(_root, "scratch"));
    }

    [Fact]
    public async Task Docx_Is_Created_By_The_Isolated_Worker_And_Verified_By_Reopening()
    {
        var output = Path.Combine(_workspace, "report.docx");

        var result = await ExecuteAsync(
            "document.create",
            output,
            new
            {
                blocks = new object[]
                {
                    new { type = "heading", level = 1, text = "Quarterly Report" },
                    new { type = "paragraph", text = "Prepared by the isolated worker." },
                    new { type = "table", rows = new[] { new[] { "Region", "Total" },
                        new[] { "North", "42" } } }
                },
                expect = new
                {
                    contains_text = new[] { "Quarterly Report", "North" },
                    min_tables = 1
                }
            });

        result.Verified.Should().BeTrue($"{result.FailureCode}: {result.FailureReason}");
        result.Artifact!.State.Should().Be(ArtifactState.Published);
        result.Artifact.Validation!.Reopened.Should().BeTrue();
        result.Evidence.Should().Contain(item => item.StartsWith("final_sha256=", StringComparison.Ordinal));

        // Independent confirmation from the test's own parser, not the worker's word.
        using var document = WordprocessingDocument.Open(output, isEditable: false);
        var text = string.Concat(
            document.MainDocumentPart!.Document.Body!.Descendants<W.Text>()
                .Select(item => item.Text));
        text.Should().Contain("Quarterly Report").And.Contain("North");
    }

    [Fact]
    public async Task Xlsx_Formula_Is_Written_And_Read_Back_From_The_Published_Workbook()
    {
        var output = Path.Combine(_workspace, "budget.xlsx");

        var result = await ExecuteAsync(
            "document.create",
            output,
            new
            {
                sheets = new object[]
                {
                    new
                    {
                        name = "Budget",
                        rows = new object[]
                        {
                            new object[] { "Item", "Cost" },
                            new object[] { "Server", 1200 }
                        },
                        cells = new object[]
                        {
                            new { cell = "C2", formula = "=B2*1.07" }
                        }
                    }
                },
                expect = new
                {
                    sheets = new[] { "Budget" },
                    cells = new object[] { new { sheet = "Budget", cell = "A2", value = "Server" } }
                }
            });

        result.Verified.Should().BeTrue($"{result.FailureCode}: {result.FailureReason}");
        using var workbook = new XLWorkbook(output);
        workbook.Worksheet("Budget").Cell("C2").HasFormula.Should().BeTrue();
    }

    [Fact]
    public async Task Editing_An_Existing_Document_Leaves_The_Original_Intact_When_Validation_Fails()
    {
        var target = Path.Combine(_workspace, "contract.docx");
        (await ExecuteAsync(
            "document.create",
            target,
            new { blocks = new object[] { new { type = "paragraph", text = "Original clause." } } }))
            .Verified.Should().BeTrue();
        var originalBytes = await File.ReadAllBytesAsync(target);

        // The edit itself is applicable, but the post-edit expectation cannot hold.
        var result = await ExecuteAsync(
            "document.edit",
            target,
            new
            {
                edits = new object[]
                {
                    new { op = "append_paragraph", text = "Added clause." }
                },
                expect = new { contains_text = new[] { "A CLAUSE THAT WAS NEVER WRITTEN" } }
            },
            sourcePath: target);

        result.Verified.Should().BeFalse();
        result.Class.Should().Be(WorkerOutcomeClass.FailedNoEffect);
        (await File.ReadAllBytesAsync(target)).Should().Equal(
            originalBytes,
            "a failed edit must never touch the user's file");
    }

    [Fact]
    public async Task Successful_Edit_Publishes_Atomically_And_Keeps_A_Backup()
    {
        var target = Path.Combine(_workspace, "notes.docx");
        (await ExecuteAsync(
            "document.create",
            target,
            new { blocks = new object[] { new { type = "paragraph", text = "First note." } } }))
            .Verified.Should().BeTrue();

        var result = await ExecuteAsync(
            "document.edit",
            target,
            new
            {
                edits = new object[]
                {
                    new { op = "replace_text", find = "First note.", replace = "Revised note." }
                },
                expect = new
                {
                    contains_text = new[] { "Revised note." },
                    absent_text = new[] { "First note." }
                }
            },
            sourcePath: target);

        result.Verified.Should().BeTrue($"{result.FailureCode}: {result.FailureReason}");
        result.Artifact!.BackupPath.Should().NotBeNull();
        File.Exists(result.Artifact.BackupPath!).Should().BeTrue(
            "the previous version must remain recoverable");
        using var document = WordprocessingDocument.Open(target, isEditable: false);
        string.Concat(document.MainDocumentPart!.Document.Body!.Descendants<W.Text>()
                .Select(item => item.Text))
            .Should().Contain("Revised note.").And.NotContain("First note.");
    }

    [Fact]
    public async Task Missing_Worker_Image_Fails_Closed_Instead_Of_Running_In_Process()
    {
        var documents = new DocumentWorkerService(
            new IsolatedWorkerHost(
                new WorkerImageResolver(Path.Combine(_root, "no-such-workers"))),
            _broker,
            Path.Combine(_root, "scratch-missing"));

        var result = await documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = "run",
                StepId = "step",
                AttemptId = "attempt",
                IdempotencyKey = "key",
                Operation = "document.create",
                OutputPath = Path.Combine(_workspace, "never.docx"),
                PayloadJson = "{}"
            },
            CancellationToken.None);

        result.Class.Should().Be(WorkerOutcomeClass.PreDispatchFailure);
        result.FailureCode.Should().Be("CapabilityUnavailable");
        File.Exists(Path.Combine(_workspace, "never.docx")).Should().BeFalse();
    }

    [Fact]
    public async Task Output_Outside_The_Authorized_Workspace_Is_Refused_Before_Dispatch()
    {
        var outside = Path.Combine(_root, "outside.docx");

        var result = await _documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = "run",
                StepId = "step",
                AttemptId = "attempt",
                IdempotencyKey = "key",
                Operation = "document.create",
                OutputPath = outside,
                PayloadJson = "{}"
            },
            CancellationToken.None);

        result.Class.Should().Be(WorkerOutcomeClass.PreDispatchFailure);
        result.FailureCode.Should().Be("WorkspaceAccessDenied");
        File.Exists(outside).Should().BeFalse();
    }

    [Fact]
    public async Task Cancelling_Mid_Operation_Reports_Unknown_Outcome_And_Leaves_No_Scratch()
    {
        var scratchRoot = Path.Combine(_root, "scratch-cancel");
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(120));
        var documents = new DocumentWorkerService(
            new IsolatedWorkerHost(
                new WorkerImageResolver(Path.Combine(AppContext.BaseDirectory, "workers"))),
            _broker,
            scratchRoot);

        var result = await documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = "run-cancel",
                StepId = "step",
                AttemptId = "attempt",
                IdempotencyKey = "key",
                Operation = "document.create",
                OutputPath = Path.Combine(_workspace, "cancelled.docx"),
                PayloadJson = JsonSerializer.Serialize(new
                {
                    blocks = new object[] { new { type = "paragraph", text = "x" } }
                })
            },
            cts.Token);

        // Either it finished before the deadline or it was interrupted; an
        // interruption must never be reported as a clean no-effect failure.
        if (!result.Verified)
        {
            result.Class.Should().Be(WorkerOutcomeClass.PostDispatchUnknownOutcome);
            result.RequiresReconciliation.Should().BeTrue();
        }
        Directory.Exists(scratchRoot).Should().BeFalse(
            "the worker's private scratch must be removed on every path");
    }

    private Task<DocumentOperationResult> ExecuteAsync(
        string operation,
        string outputPath,
        object payload,
        string? sourcePath = null) =>
        _documents.ExecuteAsync(
            new DocumentOperationRequest
            {
                RunId = $"run_{Guid.NewGuid():N}",
                StepId = "step_1",
                AttemptId = $"attempt_{Guid.NewGuid():N}",
                IdempotencyKey = Guid.NewGuid().ToString("N"),
                Operation = operation,
                SourcePath = sourcePath,
                OutputPath = outputPath,
                PayloadJson = JsonSerializer.Serialize(payload)
            },
            CancellationToken.None);

    public void Dispose()
    {
        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
    }
}
