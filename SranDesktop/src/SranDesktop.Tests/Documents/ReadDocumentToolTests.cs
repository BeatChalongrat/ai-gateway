using System.Text.Json;
using FluentAssertions;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using UglyToad.PdfPig.Fonts.Standard14Fonts;
using UglyToad.PdfPig.Writer;
using Xunit;

namespace SranDesktop.Tests.Documents;

/// <summary>
/// Desktop D: the read_document tool surface — the gates (PathGuard,
/// SensitiveReadGuard, size cap) and the header the model actually reads.
/// Shares the "PathGuardGrants" collection because granted roots are static.
/// </summary>
[Collection("PathGuardGrants")]
public sealed class ReadDocumentToolTests : IDisposable
{
    private readonly string _grantedDir;
    private readonly ReadDocumentTool _tool = new();
    private readonly SensitiveReadGuard _guard = new();

    public ReadDocumentToolTests()
    {
        _grantedDir = Path.Combine(Path.GetTempPath(), $"sran-rdoc-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_grantedDir);
        PathGuard.ConfigureGrantedRoots(new[] { _grantedDir });
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        try { Directory.Delete(_grantedDir, recursive: true); } catch (IOException) { }
    }

    private static JsonElement Args(object o) =>
        JsonSerializer.SerializeToElement(o);

    private string WritePdf(string name, string line)
    {
        var builder = new PdfDocumentBuilder();
        var font = builder.AddStandard14Font(Standard14Font.Helvetica);
        var page = builder.AddPage(595, 842);
        page.AddText(line, 12, new UglyToad.PdfPig.Core.PdfPoint(50, 750), font);
        var path = Path.Combine(_grantedDir, name);
        File.WriteAllBytes(path, builder.Build());
        return path;
    }

    [Fact]
    public async Task Reads_A_Pdf_In_A_Granted_Folder_And_Labels_The_Source()
    {
        var path = WritePdf("handbook.pdf", "Leave policy is thirty days");

        var result = await _tool.ExecuteAsync(Args(new { path }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        result.Content.Should().Contain("Leave policy is thirty days");
        // Header tells the model what it is holding.
        result.Content.Should().Contain("handbook.pdf").And.Contain("PDF");
    }

    [Fact]
    public async Task Paths_Outside_Every_Allowed_Root_Are_Refused()
    {
        var result = await _tool.ExecuteAsync(
            Args(new { path = "Q:\\elsewhere\\secret.pdf" }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("Access denied");
    }

    [Fact]
    public async Task The_Apps_Own_State_Stays_Unreachable()
    {
        var protectedPath = Path.Combine(
            SranDesktop.Application.Services.FolderGrantValidator.AppStateDir, "data.db");

        var result = await _tool.ExecuteAsync(
            Args(new { path = protectedPath }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("Access denied");
    }

    [Fact]
    public void Reading_Inside_A_Granted_Folder_Is_Consent_Free()
    {
        var inside = Path.Combine(_grantedDir, "report.pdf");

        _guard.RequiresConsent(_tool, JsonSerializer.Serialize(new { path = inside }))
            .Should().BeFalse();
    }

    [Fact]
    public void Reading_Outside_The_Chosen_Scope_Must_Ask_First()
    {
        // Inside the user profile (so PathGuard allows it) but outside every
        // folder the user actually granted — exactly the case FIX-F added the
        // prompt for. read_document must not become the way around it.
        var outside = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            "Documents", "payslip.pdf");

        _guard.RequiresConsent(_tool, JsonSerializer.Serialize(new { path = outside }))
            .Should().BeTrue();
    }

    [Fact]
    public async Task Unsupported_Types_Point_At_read_file()
    {
        var path = Path.Combine(_grantedDir, "logo.png");
        await File.WriteAllBytesAsync(path, new byte[] { 0x89, 0x50, 0x4E, 0x47 });

        var result = await _tool.ExecuteAsync(Args(new { path }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("read_file");
    }

    [Fact]
    public async Task Missing_Files_Fail_Before_Any_Parsing()
    {
        var result = await _tool.ExecuteAsync(
            Args(new { path = Path.Combine(_grantedDir, "nope.pdf") }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("File not found");
    }

    [Fact]
    public async Task max_chars_Is_Honoured_And_Clamped()
    {
        var path = WritePdf("long.pdf", new string('a', 3_000));

        var result = await _tool.ExecuteAsync(
            Args(new { path, max_chars = 1 }), CancellationToken.None);

        // Clamped to the 500 floor rather than returning a header with no body.
        result.Success.Should().BeTrue(result.Error);
        result.Content.Length.Should().BeGreaterThan(100);
    }

    [Fact]
    public void Declares_Itself_A_Read_Only_FileSystem_Tool()
    {
        _tool.Name.Should().Be("read_document");
        _tool.Category.Should().Be(ToolCategory.FileSystem);
        _tool.DefaultPermission.Should().Be(PermissionLevel.AllowAlways);
        // The description has to steer the model off read_file for documents.
        _tool.Description.Should().Contain("read_file");
        _tool.Description.Should().Contain("PDF");
    }
}
