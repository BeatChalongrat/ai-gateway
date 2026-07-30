using System.Text;
using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Workers;
using Xunit;

namespace SranDesktop.Tests.Documents;

/// <summary>
/// What this build does to each document format, proven rather than described.
///
/// PDF is the case that matters here. It is read-only, and the honest way to say
/// so is for every write-shaped operation to come back with a named refusal that
/// explains the limit — not with a generic failure that reads like a bug and
/// invites the caller to try again. The matrix is data both the host and the
/// sandboxed worker compile against, so the two cannot drift into disagreeing
/// about what is possible.
/// </summary>
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class DocumentCapabilityTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-doc-cap-{Guid.NewGuid():N}");
    private readonly string _workspace;
    private readonly DocumentWorkerService _documents;

    public DocumentCapabilityTests()
    {
        _workspace = Path.Combine(_root, "workspace");
        Directory.CreateDirectory(_workspace);
        _documents = new DocumentWorkerService(
            new IsolatedWorkerHost(
                new WorkerImageResolver(
                    Path.Combine(AppContext.BaseDirectory, "workers"))),
            new WorkspaceFileBroker(
                [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWrite)],
                _workspace),
            Path.Combine(_root, "scratch"));
    }

    // ── The matrix itself ───────────────────────────────────────────────────

    [Theory]
    [InlineData("docx", DocumentOperationKind.Create, true)]
    [InlineData("docx", DocumentOperationKind.Edit, true)]
    [InlineData("xlsx", DocumentOperationKind.Create, true)]
    [InlineData("xlsx", DocumentOperationKind.Edit, true)]
    [InlineData("pptx", DocumentOperationKind.Create, true)]
    [InlineData("pptx", DocumentOperationKind.Edit, true)]
    [InlineData("pdf", DocumentOperationKind.Inspect, true)]
    [InlineData("pdf", DocumentOperationKind.Validate, true)]
    [InlineData("pdf", DocumentOperationKind.Create, false)]
    [InlineData("pdf", DocumentOperationKind.Edit, false)]
    [InlineData("pdf", DocumentOperationKind.Split, false)]
    [InlineData("pdf", DocumentOperationKind.Merge, false)]
    [InlineData("pdf", DocumentOperationKind.Render, false)]
    [InlineData("docx", DocumentOperationKind.Render, false)]
    public void The_Matrix_Says_Exactly_What_This_Build_Does(
        string format, DocumentOperationKind operation, bool supported) =>
        DocumentCapabilityMatrix.IsSupported(format, operation).Should().Be(supported);

    [Fact]
    public void Every_Unsupported_Pair_Explains_Itself()
    {
        foreach (var format in DocumentCapabilityMatrix.Formats)
        foreach (var operation in Enum.GetValues<DocumentOperationKind>())
        {
            if (DocumentCapabilityMatrix.IsSupported(format, operation))
                continue;
            var reason = DocumentCapabilityMatrix.UnsupportedReason(format, operation);
            reason.Should().NotBeNullOrWhiteSpace();
            reason.Should().NotBe("Unsupported.",
                $"{format}/{operation} needs a reason somebody can act on");
        }
    }

    // ── PDF, through the real worker ────────────────────────────────────────

    [Fact]
    public async Task A_Pdf_Can_Be_Inspected_And_Its_Page_Count_Read()
    {
        var pdf = Path.Combine(_workspace, "sample.pdf");
        await File.WriteAllBytesAsync(pdf, MinimalPdf(pages: 2));

        var result = await ExecuteAsync("document.inspect", pdf, new { }, sourcePath: pdf);

        result.Verified.Should().BeTrue(result.FailureReason);
        // Exact, not "contains a 2": the loose form passed for years off an
        // incidental digit in the byte count while the parser was reporting three
        // pages for a two-page file.
        Report(result).Should().Contain("pages=2",
            "the structural parser reports the page count it found, and the "
            + "page-tree node is not a page");
    }

    [Fact]
    public async Task Creating_A_Pdf_Is_A_Named_Refusal_Not_A_Failure()
    {
        var output = Path.Combine(_workspace, "generated.pdf");

        var result = await ExecuteAsync("document.create", output, new { title = "x" });

        result.Verified.Should().BeFalse();
        result.FailureCode.Should().Be("CapabilityUnavailable");
        result.FailureReason.Should().Contain("cannot write");
        result.FailureReason.Should().Contain("Inspect",
            "a refusal should say what IS possible");
        File.Exists(output).Should().BeFalse();
    }

    [Theory]
    [InlineData("document.split")]
    [InlineData("document.merge")]
    [InlineData("document.render")]
    public async Task Unimplemented_Document_Operations_Are_Structured_Refusals(
        string operation)
    {
        var pdf = Path.Combine(_workspace, "sample.pdf");
        await File.WriteAllBytesAsync(pdf, MinimalPdf(pages: 1));

        var result = await ExecuteAsync(
            operation, Path.Combine(_workspace, "out.pdf"), new { }, sourcePath: pdf);

        result.Verified.Should().BeFalse();
        result.FailureCode.Should().Be("CapabilityUnavailable");
        result.FailureReason.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    public async Task A_Pdf_That_Declares_JavaScript_Is_Reported_And_Not_Executed()
    {
        var pdf = Path.Combine(_workspace, "active.pdf");
        await File.WriteAllBytesAsync(pdf, PdfWithActiveContent());

        var result = await ExecuteAsync("document.inspect", pdf, new { }, sourcePath: pdf);

        result.Verified.Should().BeTrue(result.FailureReason);
        Report(result).Should().Contain("pdf_active_content");
        Report(result).Should().Contain("NOT executed");
    }

    [Fact]
    public async Task An_Encrypted_Pdf_Is_Refused_Rather_Than_Guessed_At()
    {
        var pdf = Path.Combine(_workspace, "locked.pdf");
        await File.WriteAllBytesAsync(pdf, EncryptedPdf());

        var result = await ExecuteAsync("document.inspect", pdf, new { }, sourcePath: pdf);

        result.Verified.Should().BeFalse();
        Report(result).Should().Contain("encrypted",
            because: "a password nobody supplied is not something to brute-force");
    }

    [Fact]
    public async Task A_File_That_Is_Not_A_Pdf_Is_Rejected_On_Its_Bytes_Not_Its_Name()
    {
        var pdf = Path.Combine(_workspace, "actually-a-zip.pdf");
        await File.WriteAllBytesAsync(pdf, "PK this is a zip"u8.ToArray());

        var result = await ExecuteAsync("document.inspect", pdf, new { }, sourcePath: pdf);

        result.Verified.Should().BeFalse();
        Report(result).Should().Contain("signature");
    }

    [Fact]
    public async Task A_Truncated_Pdf_Fails_Closed_Without_Crashing_The_Worker()
    {
        var pdf = Path.Combine(_workspace, "truncated.pdf");
        await File.WriteAllBytesAsync(pdf, "%PDF-1.7\n1 0 obj\n<< /Type /Cat"u8.ToArray());

        var result = await ExecuteAsync("document.inspect", pdf, new { }, sourcePath: pdf);

        result.Verified.Should().BeFalse();
        result.FailureCode.Should().NotBe("WorkerCrashed",
            "malformed input is data, and the parser has to survive it");
    }

    /// <summary>Everything the worker said about the operation, in one string.
    /// The validator's findings travel as evidence lines, and a refusal travels
    /// as a failure reason; a test asserting on "what was reported" should not
    /// have to know which of the two carried it.</summary>
    private static string Report(DocumentOperationResult result) =>
        result.FailureCode + " " + result.FailureReason + " "
        + string.Join(" | ", result.Evidence);

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

    // ── Minimal PDFs, written by hand ───────────────────────────────────────

    private static byte[] MinimalPdf(int pages)
    {
        var kids = string.Join(" ", Enumerable.Range(0, pages).Select(index => $"{3 + index} 0 R"));
        var builder = new StringBuilder();
        builder.Append("%PDF-1.7\n");
        builder.Append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
        builder.Append(
            $"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {pages} >>\nendobj\n");
        for (var index = 0; index < pages; index++)
            builder.Append(
                $"{3 + index} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n");
        builder.Append("xref\n0 1\n0000000000 65535 f \n");
        builder.Append($"trailer\n<< /Size {3 + pages} /Root 1 0 R >>\nstartxref\n0\n%%EOF");
        return Encoding.ASCII.GetBytes(builder.ToString());
    }

    private static byte[] PdfWithActiveContent()
    {
        var builder = new StringBuilder();
        builder.Append("%PDF-1.7\n");
        builder.Append(
            "1 0 obj\n<< /Type /Catalog /Pages 2 0 R "
            + "/Names << /JavaScript 5 0 R >> /OpenAction << /S /JavaScript "
            + "/JS (app.alert('hello')) >> >>\nendobj\n");
        builder.Append("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n");
        builder.Append(
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n");
        builder.Append("xref\n0 1\n0000000000 65535 f \n");
        builder.Append("trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n0\n%%EOF");
        return Encoding.ASCII.GetBytes(builder.ToString());
    }

    private static byte[] EncryptedPdf()
    {
        var builder = new StringBuilder();
        builder.Append("%PDF-1.7\n");
        builder.Append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
        builder.Append("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n");
        builder.Append(
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n");
        builder.Append("4 0 obj\n<< /Filter /Standard /V 2 /R 3 /Length 128 >>\nendobj\n");
        builder.Append("xref\n0 1\n0000000000 65535 f \n");
        builder.Append("trailer\n<< /Size 5 /Root 1 0 R /Encrypt 4 0 R >>\nstartxref\n0\n%%EOF");
        return Encoding.ASCII.GetBytes(builder.ToString());
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_root))
                Directory.Delete(_root, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }
}
