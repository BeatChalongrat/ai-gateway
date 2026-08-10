using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 12 Task 2 — desktop project settings (instructions + knowledge base).
///
/// Phase 11 retired the desktop's LOCAL project store, which left server
/// projects rename/delete-only. This restores editing on the SHARED data, so
/// the contract that matters is: it must go through the same endpoints the
/// web Projects page uses, and it must not invent its own validation.
/// </summary>
public sealed class ProjectSettingsTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();

    private static ProjectDto Project(string? instructions = "", string? kbId = null) =>
        new("p1", "Work", instructions, kbId, 0,
            DateTimeOffset.UtcNow, DateTimeOffset.UtcNow);

    private ProjectSettingsViewModel Create(ProjectDto? project = null)
    {
        _api.Setup(a => a.ListKnowledgeBasesAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new KnowledgeBaseListResponse(new List<KnowledgeBaseDto>
            {
                new("kb1", "Handbook", 3, 40),
                new("kb2", "Contracts", 1, 12),
            }));
        return new ProjectSettingsViewModel(
            _api.Object, NullLogger.Instance, project ?? Project());
    }

    [Fact]
    public async Task Loads_The_Knowledge_Bases_With_A_None_Option_First()
    {
        var vm = Create();

        await vm.LoadAsync();

        vm.KnowledgeBases.Should().HaveCount(3);
        vm.KnowledgeBases[0].Id.Should().BeNull("detaching must always be possible");
        vm.KnowledgeBases.Skip(1).Select(k => k.Id).Should().Equal("kb1", "kb2");
    }

    [Fact]
    public async Task Preselects_The_Project_Current_Knowledge_Base()
    {
        var vm = Create(Project(kbId: "kb2"));

        await vm.LoadAsync();

        vm.SelectedKnowledgeBase!.Id.Should().Be("kb2");
    }

    [Fact]
    public async Task A_Failing_KB_List_Still_Leaves_The_Instructions_Editable()
    {
        // The two halves of the dialog are independent — losing the KB list
        // must not block someone who came to edit text.
        _api.Setup(a => a.ListKnowledgeBasesAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var vm = new ProjectSettingsViewModel(
            _api.Object, NullLogger.Instance, Project("keep me"));

        await vm.LoadAsync();

        vm.Instructions.Should().Be("keep me");
        vm.StatusMessage.Should().NotBeNullOrEmpty();
        vm.KnowledgeBases.Should().ContainSingle().Which.Id.Should().BeNull();
    }

    [Fact]
    public async Task Saves_Instructions_Through_The_Same_Endpoint_The_Web_Page_Uses()
    {
        var vm = Create();
        await vm.LoadAsync();
        vm.Instructions = "ตอบสุภาพ ลงท้ายด้วยครับ";

        await vm.SaveCommand.ExecuteAsync(null);

        _api.Verify(a => a.UpdateProjectInstructionsAsync(
            "p1", "ตอบสุภาพ ลงท้ายด้วยครับ", It.IsAny<CancellationToken>()), Times.Once);
        vm.Saved.Should().BeTrue();
    }

    [Fact]
    public async Task Only_Calls_The_KB_Endpoint_When_The_Selection_Actually_Changed()
    {
        var vm = Create(Project(kbId: "kb1"));
        await vm.LoadAsync();

        await vm.SaveCommand.ExecuteAsync(null);

        _api.Verify(a => a.SetProjectKnowledgeBaseAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Attaches_And_Detaches_The_Knowledge_Base()
    {
        var attach = Create();
        await attach.LoadAsync();
        attach.SelectedKnowledgeBase = attach.KnowledgeBases.First(k => k.Id == "kb2");
        await attach.SaveCommand.ExecuteAsync(null);
        _api.Verify(a => a.SetProjectKnowledgeBaseAsync("p1", "kb2", It.IsAny<CancellationToken>()), Times.Once);

        _api.Invocations.Clear();
        var detach = Create(Project(kbId: "kb2"));
        await detach.LoadAsync();
        detach.SelectedKnowledgeBase = detach.KnowledgeBases.First(k => k.Id is null);
        await detach.SaveCommand.ExecuteAsync(null);
        _api.Verify(a => a.SetProjectKnowledgeBaseAsync("p1", null, It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Refuses_Instructions_Longer_Than_The_Server_Cap_Before_Sending()
    {
        var vm = Create();
        await vm.LoadAsync();
        vm.Instructions = new string('x', ProjectSettingsViewModel.MaxInstructions + 1);

        vm.IsTooLong.Should().BeTrue();
        await vm.SaveCommand.ExecuteAsync(null);

        _api.Verify(a => a.UpdateProjectInstructionsAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        vm.Saved.Should().BeFalse();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task A_Server_Failure_Reports_And_Does_Not_Claim_Success()
    {
        _api.Setup(a => a.UpdateProjectInstructionsAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("500"));
        var vm = Create();
        await vm.LoadAsync();
        vm.Instructions = "x";

        await vm.SaveCommand.ExecuteAsync(null);

        vm.Saved.Should().BeFalse();
        vm.StatusMessage.Should().NotBeNullOrEmpty();
        vm.CanSave.Should().BeTrue("the dialog must stay usable after a failure");
    }

    [Fact]
    public void Exposes_A_Live_Character_Count_For_The_Editor()
    {
        var vm = Create(Project("abc"));
        vm.InstructionsLength.Should().Be(3);
        vm.Instructions = "abcdef";
        vm.InstructionsLength.Should().Be(6);
        vm.IsTooLong.Should().BeFalse();
    }

    [Fact]
    public void The_Dialog_Strings_Exist_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "project.settings", "project.settings.subtitle",
                     "project.instructions", "project.instructionsHint",
                     "project.instructions.tooLong", "project.kb", "project.kbHint",
                     "project.kb.none", "project.kb.loadFailed", "project.saveFailed",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
        }
    }
}
