using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 15 Tasks 2 + 3 — editing and version history in the Canvas.
///
/// Versioning is server-backed through the SAME canvas_artifacts endpoints the
/// web canvas uses, so these tests double as the sync contract: the desktop
/// must write the lineage the web reads.
/// </summary>
public sealed class CanvasViewModelTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();

    private const string Conv = "conv-1";
    private const string Msg = "msg-1";

    private CanvasViewModel Create(
        string type = "html", string source = "<p>v1</p>",
        string? conversationId = Conv) =>
        new(_api.Object, NullLogger.Instance, type, source, "artifact.html", conversationId, Msg);

    private static CanvasArtifactDto Dto(int version, string content, string type = "html") =>
        new($"id-{version}", Conv, "key", type, content, version,
            DateTimeOffset.UtcNow.AddMinutes(version), Msg, "artifact.html");

    private void Versions(params CanvasArtifactDto[] items) =>
        _api.Setup(a => a.ListCanvasVersionsAsync(Conv, It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new CanvasVersionListResponse(items.ToList()));

    // ── Task 2: edit + re-render ───────────────────────────────────────────

    [Fact]
    public void Opening_A_Canvas_Shows_The_Preview_Not_The_Code()
    {
        var vm = Create();

        vm.IsPreviewMode.Should().BeTrue("the rendered result is what the user asked for");
        vm.IsCodeMode.Should().BeFalse();
        vm.IsDirty.Should().BeFalse();
    }

    [Fact]
    public void Editing_Marks_Dirty_But_Does_Not_Re_Render_Until_Committed()
    {
        var vm = Create();
        var renders = 0;
        vm.RenderRequested += (_, _) => renders++;

        vm.Draft = "<p>edited</p>";

        vm.IsDirty.Should().BeTrue();
        renders.Should().Be(0, "the debounce owns when a commit happens");
        vm.Source.Should().Be("<p>v1</p>", "the preview still shows the committed source");
    }

    [Fact]
    public void Committing_A_Draft_Re_Renders_From_The_New_Source()
    {
        var vm = Create();
        var renders = 0;
        vm.RenderRequested += (_, _) => renders++;
        vm.Draft = "<p>edited</p>";

        vm.CommitDraft();

        renders.Should().Be(1);
        vm.Source.Should().Be("<p>edited</p>");
        vm.IsDirty.Should().BeFalse();
    }

    [Fact]
    public void Committing_An_Unchanged_Draft_Does_Nothing()
    {
        var vm = Create();
        var renders = 0;
        vm.RenderRequested += (_, _) => renders++;

        vm.CommitDraft();

        renders.Should().Be(0);
    }

    [Fact]
    public void An_Edit_Renders_Through_The_Same_Sandbox_With_The_Same_Posture()
    {
        // The security posture must not soften after an edit: the edited
        // source goes through the identical builder + CSP.
        var vm = Create();
        vm.Draft = "<script>fetch('https://evil')</script>";
        vm.CommitDraft();

        var html = CanvasHtmlBuilder.Build(vm.Type, vm.Source, vm.Title, dark: false, "tok");

        html.Should().Contain("connect-src 'none'");
        html.Should().Contain(CanvasSandbox.CspMetaTag());
    }

    [Fact]
    public void A_Runtime_Error_Is_Surfaced_And_Cleared_On_The_Next_Commit()
    {
        var vm = Create();

        vm.ReportRuntimeError("ReferenceError: x is not defined");
        vm.HasRuntimeError.Should().BeTrue();
        vm.RuntimeError.Should().Contain("ReferenceError");

        vm.Draft = "<p>fixed</p>";
        vm.CommitDraft();
        vm.HasRuntimeError.Should().BeFalse("the error belonged to the previous source");
    }

    [Fact]
    public void An_Empty_Error_Payload_Still_Says_Something_Useful()
    {
        var vm = Create();

        vm.ReportRuntimeError("   ");

        vm.RuntimeError.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    public void Toggling_Code_Mode_Flips_Both_Views()
    {
        var vm = Create();

        vm.ToggleCodeModeCommand.Execute(null);

        vm.IsCodeMode.Should().BeTrue();
        vm.IsPreviewMode.Should().BeFalse();
    }

    // ── Task 3: versioning ─────────────────────────────────────────────────

    [Fact]
    public async Task Opening_An_Unknown_Block_Adopts_It_As_Version_One()
    {
        // Without adoption nothing server-side identifies the artifact, so the
        // model could never revise it and the web would never see it.
        Versions();
        _api.Setup(a => a.SaveCanvasArtifactAsync(Conv, It.IsAny<SaveCanvasArtifactRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Dto(1, "<p>v1</p>"));
        var vm = Create();

        await vm.LoadVersionsAsync();

        _api.Verify(a => a.SaveCanvasArtifactAsync(Conv,
            It.Is<SaveCanvasArtifactRequest>(r =>
                r.ArtifactKey == vm.ArtifactKey && r.Content == "<p>v1</p>" && r.MessageId == Msg),
            It.IsAny<CancellationToken>()), Times.Once);
        vm.Versions.Should().ContainSingle().Which.Version.Should().Be(1);
    }

    [Fact]
    public async Task An_Existing_Lineage_Loads_And_Adopts_The_Newest_Content()
    {
        // This is the sync direction that matters: a version written on the
        // WEB is what the desktop opens.
        Versions(Dto(2, "<p>edited on web</p>"), Dto(1, "<p>v1</p>"));
        var vm = Create();

        await vm.LoadVersionsAsync();

        vm.Versions.Should().HaveCount(2);
        vm.Source.Should().Be("<p>edited on web</p>");
        vm.Draft.Should().Be("<p>edited on web</p>");
        _api.Verify(a => a.SaveCanvasArtifactAsync(
            It.IsAny<string>(), It.IsAny<SaveCanvasArtifactRequest>(), It.IsAny<CancellationToken>()),
            Times.Never, "an existing lineage is never re-adopted");
    }

    [Fact]
    public async Task A_History_Load_Never_Clobbers_Unsaved_Edits()
    {
        Versions(Dto(1, "<p>server</p>"));
        var vm = Create();
        vm.Draft = "<p>my unsaved work</p>";

        await vm.LoadVersionsAsync();

        vm.Draft.Should().Be("<p>my unsaved work</p>");
        vm.Versions.Should().HaveCount(1, "the history still loads");
    }

    [Fact]
    public async Task Saving_Appends_The_Next_Version()
    {
        _api.Setup(a => a.SaveCanvasArtifactAsync(Conv, It.IsAny<SaveCanvasArtifactRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Dto(2, "<p>v2</p>"));
        var vm = Create();
        vm.Draft = "<p>v2</p>";

        await vm.SaveVersionCommand.ExecuteAsync(null);

        vm.Versions.Should().ContainSingle().Which.Version.Should().Be(2);
        vm.Source.Should().Be("<p>v2</p>", "saving commits the draft first");
        vm.IsDirty.Should().BeFalse();
    }

    [Fact]
    public async Task Revert_Appends_Rather_Than_Rewriting_History()
    {
        // The server appends the old content as a NEW version, so an
        // accidental revert is itself revertable.
        _api.Setup(a => a.RevertCanvasArtifactAsync(Conv, It.IsAny<string>(), 1, It.IsAny<CancellationToken>()))
            .ReturnsAsync(Dto(3, "<p>v1</p>"));
        var vm = Create();
        var renders = 0;
        vm.RenderRequested += (_, _) => renders++;
        var row = new CanvasVersionRow(1, "<p>v1</p>", "html", DateTimeOffset.UtcNow);

        await vm.RevertCommand.ExecuteAsync(row);

        vm.Versions.Should().ContainSingle().Which.Version.Should().Be(3);
        vm.Source.Should().Be("<p>v1</p>");
        vm.Draft.Should().Be("<p>v1</p>");
        renders.Should().Be(1, "the preview follows the revert");
    }

    [Fact]
    public async Task Revert_Ignores_A_Wrong_Type_Parameter()
    {
        // FIX 12: the command is bound from a templated row.
        var vm = Create();

        vm.RevertCommand.CanExecute("a string").Should().BeTrue();
        await vm.RevertCommand.ExecuteAsync("a string");

        _api.Verify(a => a.RevertCanvasArtifactAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<int>(), It.IsAny<CancellationToken>()),
            Times.Never);
    }

    [Fact]
    public async Task A_Failed_History_Load_Leaves_The_Preview_Working()
    {
        _api.Setup(a => a.ListCanvasVersionsAsync(Conv, It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var vm = Create();

        await vm.LoadVersionsAsync();

        vm.Source.Should().Be("<p>v1</p>", "history is an enhancement, the preview is the point");
        vm.Versions.Should().BeEmpty();
        vm.IsBusy.Should().BeFalse();
    }

    [Fact]
    public async Task A_Failed_Save_Reports_And_Keeps_The_Editor_Usable()
    {
        _api.Setup(a => a.SaveCanvasArtifactAsync(Conv, It.IsAny<SaveCanvasArtifactRequest>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("500"));
        var vm = Create();
        vm.Draft = "<p>v2</p>";

        await vm.SaveVersionCommand.ExecuteAsync(null);

        vm.Versions.Should().BeEmpty();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
        vm.IsBusy.Should().BeFalse();
    }

    [Fact]
    public async Task A_Local_Only_Chat_Previews_And_Edits_But_Cannot_Persist()
    {
        // An agent conversation with no server row has nowhere to save — the
        // canvas must still open and edit rather than refusing.
        var vm = Create(conversationId: null);

        vm.CanPersist.Should().BeFalse();
        vm.Draft = "<p>edited</p>";
        vm.CommitDraft();
        vm.Source.Should().Be("<p>edited</p>");

        await vm.LoadVersionsAsync();
        await vm.SaveVersionCommand.ExecuteAsync(null);

        _api.Verify(a => a.ListCanvasVersionsAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        _api.Verify(a => a.SaveCanvasArtifactAsync(
            It.IsAny<string>(), It.IsAny<SaveCanvasArtifactRequest>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public void The_Lineage_Key_Comes_From_The_ORIGINAL_Source()
    {
        // Keying off the live draft would start a new history on every edit.
        var vm = Create(source: "<p>original</p>");
        var before = vm.ArtifactKey;

        vm.Draft = "<p>heavily rewritten</p>";
        vm.CommitDraft();

        vm.ArtifactKey.Should().Be(before);
    }

    [Fact]
    public void Canvas_Strings_Exist_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "canvas.open", "canvas.showCode", "canvas.showPreview", "canvas.copy",
                     "canvas.download", "canvas.versions", "canvas.saveVersion", "canvas.revert",
                     "canvas.saved", "canvas.saveFailed", "canvas.revertFailed", "canvas.copied",
                     "canvas.runtimeError", "canvas.noWebView2",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
        }
    }
}
