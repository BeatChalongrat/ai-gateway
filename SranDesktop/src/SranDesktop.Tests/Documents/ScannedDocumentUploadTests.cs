using System.Text.Json;
using FluentAssertions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using UglyToad.PdfPig.Fonts.Standard14Fonts;
using UglyToad.PdfPig.Writer;
using Xunit;

namespace SranDesktop.Tests.Documents;

/// <summary>
/// Scanned PDFs have no text layer, so read_document sends them to chat-edge's
/// OCR and hands the text back. There is deliberately NO per-upload prompt:
/// the model already runs on the gateway, so everything this tool reads reaches
/// the server as prompt text either way — a scan just travels as image bytes.
/// The gates that decide which files the agent may touch (PathGuard, granted
/// folders, SensitiveReadGuard) are unchanged and still run first; the user
/// gets a one-time notice, not a decision per file.
/// </summary>
[Collection("PathGuardGrants")]
public sealed class ScannedDocumentUploadTests : IDisposable
{
    private readonly string _grantedDir;
    private readonly Mock<IScannedDocumentOcr> _ocr = new();
    private readonly FakeSettings _settings = new();
    private readonly RecordingToasts _toasts = new();

    private sealed class FakeSettings : ISettingsService
    {
        public UserSettings Current { get; private set; } = new();
        public bool IsFirstRun => false;
        public int Saves { get; private set; }
        public Exception? SaveThrows { get; set; }
        public event EventHandler? SettingsChanged;

        public void Save(UserSettings settings)
        {
            Saves++;
            if (SaveThrows is not null)
                throw SaveThrows;
            Current = settings;
            SettingsChanged?.Invoke(this, EventArgs.Empty);
        }
    }

    private sealed class RecordingToasts : IToastService
    {
        public readonly List<string> Keys = new();

        public void Show(ToastKind kind, string messageKey, string? detail = null, TimeSpan? duration = null)
        {
            kind.Should().Be(ToastKind.Info, "the notice is informational, not an error");
            Keys.Add(messageKey);
        }
    }

    public ScannedDocumentUploadTests()
    {
        _grantedDir = Path.Combine(Path.GetTempPath(), $"sran-scan-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_grantedDir);
        PathGuard.ConfigureGrantedRoots(new[] { _grantedDir });

        _ocr.Setup(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ScannedOcrOutcome.Ok("[OCR] เรียน ผู้จัดการ", truncated: false));
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        try { Directory.Delete(_grantedDir, recursive: true); } catch (IOException) { }
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    /// <summary>A page with no text operators — what a scan looks like to a parser.</summary>
    private string WriteScan(string name = "scan.pdf")
    {
        var builder = new PdfDocumentBuilder();
        builder.AddPage(595, 842);
        var path = Path.Combine(_grantedDir, name);
        File.WriteAllBytes(path, builder.Build());
        return path;
    }

    private string WriteTextPdf(string name = "memo.pdf")
    {
        var builder = new PdfDocumentBuilder();
        var font = builder.AddStandard14Font(Standard14Font.Helvetica);
        var page = builder.AddPage(595, 842);
        page.AddText("Quarterly revenue is up", 12, new UglyToad.PdfPig.Core.PdfPoint(50, 750), font);
        var path = Path.Combine(_grantedDir, name);
        File.WriteAllBytes(path, builder.Build());
        return path;
    }

    /// <summary>The tool wired the way the app wires it.</summary>
    private ReadDocumentTool BuildTool() => new(_ocr.Object, _settings, _toasts);

    [Fact]
    public async Task A_Scan_Is_Read_By_Server_Ocr_Without_Asking()
    {
        var result = await BuildTool().ExecuteAsync(
            Args(new { path = WriteScan() }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        result.Content.Should().Contain("เรียน ผู้จัดการ");
        // The model has to know this text came from OCR, not from the file.
        result.Content.Should().Contain("OCR");
        _ocr.Verify(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task The_Notice_Appears_On_The_First_Scan_Only()
    {
        var tool = BuildTool();

        await tool.ExecuteAsync(Args(new { path = WriteScan("a.pdf") }), CancellationToken.None);
        await tool.ExecuteAsync(Args(new { path = WriteScan("b.pdf") }), CancellationToken.None);

        _toasts.Keys.Should().ContainSingle().Which.Should().Be("toast.scannedUploadNotice");
    }

    [Fact]
    public async Task The_Notice_Does_Not_Come_Back_After_A_Restart()
    {
        await BuildTool().ExecuteAsync(Args(new { path = WriteScan("today.pdf") }), CancellationToken.None);
        _settings.Current.ScannedUploadNoticeShown.Should().BeTrue("the flag is persisted");

        // A fresh tool over the same saved settings — i.e. tomorrow morning.
        await BuildTool().ExecuteAsync(Args(new { path = WriteScan("tomorrow.pdf") }), CancellationToken.None);

        _toasts.Keys.Should().ContainSingle();
    }

    [Fact]
    public async Task A_Settings_Write_Failure_Never_Breaks_The_Read()
    {
        _settings.SaveThrows = new IOException("settings file is locked");

        var result = await BuildTool().ExecuteAsync(
            Args(new { path = WriteScan() }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        result.Content.Should().Contain("เรียน ผู้จัดการ");
        _settings.Saves.Should().Be(1, "it tried");
    }

    [Fact]
    public async Task A_Document_With_Text_Is_Read_Locally_And_Silently()
    {
        var result = await BuildTool().ExecuteAsync(
            Args(new { path = WriteTextPdf() }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        result.Content.Should().Contain("Quarterly revenue is up");
        _toasts.Keys.Should().BeEmpty("nothing was uploaded, so there is nothing to disclose");
        _ocr.Verify(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Without_An_Ocr_Service_The_Tool_Stays_Strictly_Offline()
    {
        var result = await new ReadDocumentTool().ExecuteAsync(
            Args(new { path = WriteScan() }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("no extractable text layer").And.Contain("OCR");
    }

    [Fact]
    public async Task Without_A_Toast_Host_The_Scan_Still_Reads()
    {
        var tool = new ReadDocumentTool(_ocr.Object, settings: null, toasts: null);

        var result = await tool.ExecuteAsync(Args(new { path = WriteScan() }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
    }

    [Fact]
    public async Task An_Ocr_Failure_Reaches_The_Model_Verbatim()
    {
        _ocr.Setup(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ScannedOcrOutcome.Failed("needs a connection to the SRAN server"));

        var result = await BuildTool().ExecuteAsync(
            Args(new { path = WriteScan() }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("needs a connection to the SRAN server");
    }

    [Fact]
    public async Task Truncated_Ocr_Output_Is_Labelled_As_Partial()
    {
        _ocr.Setup(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ScannedOcrOutcome.Ok("page one of many", truncated: true));

        var result = await BuildTool().ExecuteAsync(
            Args(new { path = WriteScan() }), CancellationToken.None);

        result.Content.Should().Contain("TRUNCATED");
    }

    [Fact]
    public async Task Ocr_Text_Still_Honours_The_Callers_Char_Budget()
    {
        _ocr.Setup(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ScannedOcrOutcome.Ok(new string('ก', 5_000), truncated: false));

        var result = await BuildTool().ExecuteAsync(
            Args(new { path = WriteScan(), max_chars = 500 }), CancellationToken.None);

        result.Success.Should().BeTrue(result.Error);
        result.Content.Count(c => c == 'ก').Should().Be(500);
        result.Content.Should().Contain("TRUNCATED");
    }

    // Dropping the upload prompt must not widen what the agent may open. A scan
    // outside the user's granted folders is still gated by the guard that has
    // always covered read_document.
    [Fact]
    public void A_Scan_Outside_The_Granted_Folders_Still_Has_To_Ask()
    {
        var guard = new SensitiveReadGuard();
        var outside = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            "Documents", "payslip-scan.pdf");

        guard.RequiresConsent(BuildTool(), JsonSerializer.Serialize(new { path = outside }))
            .Should().BeTrue();
        guard.RequiresConsent(BuildTool(), JsonSerializer.Serialize(new { path = WriteScan() }))
            .Should().BeFalse("files in a folder the user granted are read without friction");
    }

    [Fact]
    public async Task Paths_PathGuard_Refuses_Never_Reach_The_Uploader()
    {
        var result = await BuildTool().ExecuteAsync(
            Args(new { path = "Q:\\elsewhere\\secret-scan.pdf" }), CancellationToken.None);

        result.Success.Should().BeFalse();
        _ocr.Verify(o => o.ExtractAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }
}
