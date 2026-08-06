using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 14 Task 2 — which preview an attachment gets.
///
/// The default is deliberately "unsupported": guessing wrong sends an
/// executable into a document viewer, while guessing unsupported only costs
/// the user one click on Download.
/// </summary>
public sealed class AttachmentPreviewRouterTests
{
    [Theory]
    [InlineData("image/png", "shot.png")]
    [InlineData("image/jpeg", "photo.jpg")]
    [InlineData("IMAGE/WEBP", "x.webp")]
    public void Images_Open_The_Image_Viewer(string type, string name)
    {
        AttachmentPreviewRouter.Resolve(type, name).Should().Be(AttachmentPreviewKind.Image);
    }

    [Fact]
    public void Pdfs_Open_The_Document_Viewer()
    {
        AttachmentPreviewRouter.Resolve("application/pdf", "report.pdf")
            .Should().Be(AttachmentPreviewKind.Pdf);
    }

    [Theory]
    [InlineData("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx")]
    [InlineData("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "a.xlsx")]
    [InlineData("application/vnd.openxmlformats-officedocument.presentationml.presentation", "a.pptx")]
    [InlineData("application/msword", "a.doc")]
    [InlineData("text/plain; charset=utf-8", "notes.txt")]
    [InlineData("text/csv", "rows.csv")]
    public void Documents_Show_The_Extracted_Text(string type, string name)
    {
        AttachmentPreviewRouter.Resolve(type, name)
            .Should().Be(AttachmentPreviewKind.ExtractedText);
    }

    [Theory]
    [InlineData("application/zip", "bundle.zip")]
    [InlineData("application/x-msdownload", "setup.exe")]
    [InlineData("application/octet-stream", "firmware.bin")]
    [InlineData(null, "mystery")]
    public void Everything_Else_Says_So_Plainly(string? type, string name)
    {
        AttachmentPreviewRouter.Resolve(type, name)
            .Should().Be(AttachmentPreviewKind.Unsupported);
    }

    [Theory]
    [InlineData("report.pdf", AttachmentPreviewKind.Pdf)]
    [InlineData("sheet.xlsx", AttachmentPreviewKind.ExtractedText)]
    [InlineData("pic.PNG", AttachmentPreviewKind.Image)]
    public void An_Octet_Stream_Falls_Back_To_The_Filename(string name, AttachmentPreviewKind expected)
    {
        // chat-edge uploads can arrive as octet-stream when the browser failed
        // to sniff a type; the name is then the only signal there is.
        AttachmentPreviewRouter.Resolve("application/octet-stream", name).Should().Be(expected);
    }

    [Theory]
    [InlineData("application/pdf", "payload.exe")]
    [InlineData("image/png", "installer.msi")]
    [InlineData("text/plain", "archive.zip")]
    public void A_Mislabelled_Executable_Or_Archive_Is_Never_Previewed(string type, string name)
    {
        // The extension blocklist wins over the declared content type — the
        // server's label is not trusted to open a viewer.
        AttachmentPreviewRouter.Resolve(type, name)
            .Should().Be(AttachmentPreviewKind.Unsupported);
    }

    [Fact]
    public void A_Missing_Filename_Does_Not_Throw()
    {
        AttachmentPreviewRouter.Resolve("image/png", null).Should().Be(AttachmentPreviewKind.Image);
        AttachmentPreviewRouter.Resolve(null, null).Should().Be(AttachmentPreviewKind.Unsupported);
        AttachmentPreviewRouter.Resolve("", "").Should().Be(AttachmentPreviewKind.Unsupported);
    }
}

/// <summary>Phase 14 Task 2 — reading the async extraction status.</summary>
public sealed class ExtractedTextStatusTests
{
    [Fact]
    public void Only_A_Completed_Extraction_Counts_As_Text()
    {
        ExtractedTextStatus.Resolve("completed", "hello").Should().Be(ExtractedTextState.Ready);
    }

    [Theory]
    [InlineData("pending")]
    [InlineData("processing")]
    [InlineData("queued")]
    [InlineData("")]
    [InlineData(null)]
    public void A_Non_Terminal_Status_Is_Pending_Even_With_No_Text(string? status)
    {
        // The critical one: "no text yet" must never render as an empty pane,
        // which reads as "this document is blank".
        ExtractedTextStatus.Resolve(status, null).Should().Be(ExtractedTextState.Pending);
    }

    [Fact]
    public void A_Completed_But_Empty_Extraction_Is_Its_Own_State()
    {
        ExtractedTextStatus.Resolve("completed", "   ").Should().Be(ExtractedTextState.Empty);
    }

    [Theory]
    [InlineData("failed")]
    [InlineData("error")]
    [InlineData("unsupported")]
    public void Failures_Are_Reported_As_Failures(string status)
    {
        ExtractedTextStatus.Resolve(status, null).Should().Be(ExtractedTextState.Failed);
    }
}

/// <summary>
/// Phase 14 Task 2 — the PDF sandbox posture.
///
/// A PDF attachment is untrusted, scriptable input. This pins the navigation
/// lock; the scripting switch itself lives in the dialog's code-behind
/// (settings.IsScriptEnabled = false) and is covered by the manual QA.
/// </summary>
public sealed class PdfPreviewSandboxTests
{
    private const string File1 = "abc123.pdf";

    [Fact]
    public void The_Staged_Document_Is_Allowed()
    {
        PdfPreviewSandbox.IsAllowed(PdfPreviewSandbox.UrlFor(File1), File1).Should().BeTrue();
    }

    [Theory]
    [InlineData("https://pdfpreview.sran/other.pdf")]      // a different staged file
    [InlineData("https://evil.example.com/abc123.pdf")]    // another host
    [InlineData("http://pdfpreview.sran/abc123.pdf")]      // downgraded scheme
    [InlineData("file:///C:/Windows/win.ini")]             // local filesystem
    [InlineData("javascript:alert(1)")]
    [InlineData("data:text/html,<script>alert(1)</script>")]
    [InlineData("mailto:someone@example.com")]
    [InlineData("about:blank")]
    [InlineData("")]
    [InlineData(null)]
    public void Everything_Else_Is_Denied(string? uri)
    {
        PdfPreviewSandbox.IsAllowed(uri, File1).Should().BeFalse($"uri was \"{uri}\"");
    }

    [Fact]
    public void There_Is_No_Open_In_Browser_Escape_Hatch()
    {
        // Unlike the artifact sandbox (user-authored, linking out is a
        // feature), an https link inside an untrusted attachment is simply
        // denied — it must not be able to launch the user's real browser.
        PdfPreviewSandbox.IsAllowed("https://example.com/", File1).Should().BeFalse();
    }

    [Fact]
    public void It_Uses_Its_Own_Host_Not_The_Artifact_Host()
    {
        // Sharing the artifact host would put the bundled JS libraries within
        // reach of an untrusted document.
        PdfPreviewSandbox.VirtualHost.Should().NotBe(ArtifactSandbox.VirtualHost);
        PdfPreviewSandbox.IsAllowed($"https://{ArtifactSandbox.VirtualHost}/{File1}", File1)
            .Should().BeFalse();
    }

    [Fact]
    public void The_Servers_Filename_Never_Reaches_The_Filesystem()
    {
        var name = PdfPreviewSandbox.TempFileName(Guid.NewGuid());

        name.Should().EndWith(".pdf");
        name.Should().NotContain("/").And.NotContain("\\").And.NotContain("..");
        Path.GetFileName(name).Should().Be(name);
    }
}

/// <summary>Phase 14 Task 2 — the preview view model's states.</summary>
public sealed class FilePreviewViewModelTests
{
    private static FilePreviewViewModel Create(
        string filename, string? contentType,
        Func<Task<byte[]?>>? bytes = null,
        Func<Task<FileTextDto>>? text = null) =>
        new(filename, contentType,
            bytes ?? (() => Task.FromResult<byte[]?>(Array.Empty<byte>())),
            text,
            NullLogger.Instance);

    private static Task<FileTextDto> Text(string status, string? body, bool truncated = false) =>
        Task.FromResult(new FileTextDto(status, body, truncated, false));

    [Fact]
    public async Task An_Office_Document_Shows_Its_Extracted_Text()
    {
        var vm = Create("report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            text: () => Text("completed", "Quarterly results"));

        await vm.LoadAsync();

        vm.IsText.Should().BeTrue();
        vm.Text.Should().Be("Quarterly results");
        vm.StatusMessage.Should().BeNull();
        vm.IsLoading.Should().BeFalse();
    }

    [Fact]
    public async Task A_Pending_Extraction_Says_So_Rather_Than_Showing_An_Empty_Box()
    {
        var vm = Create("report.docx", "application/msword",
            text: () => Text("processing", null));

        await vm.LoadAsync();

        vm.Text.Should().BeNull();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task A_Truncated_Extraction_Shows_The_Text_And_Says_It_Is_Partial()
    {
        var vm = Create("big.docx", "application/msword",
            text: () => Text("completed", "page one", truncated: true));

        await vm.LoadAsync();

        vm.Text.Should().Be("page one");
        vm.StatusMessage.Should().NotBeNullOrEmpty("the document does not just end there");
    }

    [Fact]
    public async Task An_Office_Document_With_No_Server_Id_Degrades_To_Download()
    {
        // No text resolver ⇒ nothing to ask for ⇒ a pane that could never fill.
        var vm = Create("local.docx", "application/msword", text: null);

        await vm.LoadAsync();

        vm.IsUnsupported.Should().BeTrue();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task A_Failed_Byte_Fetch_Reports_Instead_Of_Rendering_Nothing()
    {
        var vm = Create("a.png", "image/png",
            bytes: () => throw new HttpRequestException("offline"));

        await vm.LoadAsync();

        vm.Image.Should().BeNull();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
        vm.IsLoading.Should().BeFalse("the spinner must not stick after a failure");
    }

    [Fact]
    public async Task An_Unsupported_File_Explains_Itself()
    {
        var vm = Create("bundle.zip", "application/zip");

        await vm.LoadAsync();

        vm.IsUnsupported.Should().BeTrue();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public void A_Pdf_Can_Fall_Back_To_Text_Only_When_There_Is_A_Resolver()
    {
        Create("a.pdf", "application/pdf", text: () => Text("completed", "x"))
            .CanShowTextInstead.Should().BeTrue();
        Create("a.pdf", "application/pdf", text: null)
            .CanShowTextInstead.Should().BeFalse();
        Create("a.png", "image/png", text: () => Text("completed", "x"))
            .CanShowTextInstead.Should().BeFalse("only PDFs offer the swap");
    }

    [Fact]
    public async Task Switching_A_Pdf_To_Text_Reloads_In_The_Text_Pane()
    {
        var vm = Create("scan.pdf", "application/pdf",
            text: () => Text("completed", "OCR output"));
        vm.IsPdf.Should().BeTrue();

        await vm.ShowTextInsteadCommand.ExecuteAsync(null);

        vm.IsText.Should().BeTrue();
        vm.IsPdf.Should().BeFalse();
        vm.Text.Should().Be("OCR output");
    }

    [Fact]
    public async Task Retry_Re_Asks_A_Pending_Extraction()
    {
        var calls = 0;
        var vm = Create("a.docx", "application/msword",
            text: () =>
            {
                calls++;
                return calls == 1 ? Text("pending", null) : Text("completed", "ready now");
            });

        await vm.LoadAsync();
        vm.Text.Should().BeNull();

        await vm.RetryCommand.ExecuteAsync(null);

        vm.Text.Should().Be("ready now");
        vm.StatusMessage.Should().BeNull();
    }

    [Fact]
    public void Preview_Strings_Exist_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "attachment.preview", "preview.showText", "preview.failed",
                     "preview.unsupported", "preview.extractPending",
                     "preview.extractFailed", "preview.noText", "preview.truncated",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
        }
    }
}
