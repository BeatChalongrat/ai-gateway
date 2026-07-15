using System.Text.Json;
using DocumentFormat.OpenXml.Packaging;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Office;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 16 B: PlanBlockViewModel routing for :::pptx-plan between the
/// on-device LocalPptxPlanExecutor and the chat-edge server path — the CanExecute
/// chart exclusion and the setting gate mirror the excel/doc executors exactly.</summary>
public class PlanBlockViewModelPptxTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<ISettingsService> _settings = new();

    private static JsonElement Plan(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private static PlanExecutionResult ServerResult() => new(
        new PlanFileDto("srv-1", "deck.pptx", "application/octet-stream", 42,
            "/api/v1/files/srv-1/download", 1),
        new PlanValidationDto(true, null, null, null, null),
        new List<string> { "server rendered the deck" });

    private PlanBlockViewModel BuildVm(bool settingOn, string planJson)
    {
        _settings.SetupGet(s => s.Current).Returns(new UserSettings
        {
            PreferLocalOfficeGeneration = settingOn,
        });
        _api.Setup(a => a.ExecuteDocumentPlanAsync(
                It.IsAny<string>(), It.IsAny<ExecuteDocumentPlanRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServerResult());

        return new PlanBlockViewModel(
            _api.Object, "conv-1", "pptx", Plan(planJson),
            new LocalPptxPlanExecutor(), _settings.Object,
            NullLogger<PlanBlockViewModel>.Instance);
    }

    private const string TextDeck = """
    {
      "slides": [
        {"type": "title", "title": "On-device Deck"},
        {"type": "table", "title": "T", "headers": ["A", "B"], "rows": [["1", "2"]]}
      ]
    }
    """;

    private const string ChartDeck = """
    {
      "slides": [
        {"type": "title", "title": "Has a chart"},
        {"type": "chart", "title": "Sales", "chart_type": "bar",
         "data": {"categories": ["Q1"], "series": [{"name": "S", "values": [1]}]}}
      ]
    }
    """;

    [Fact]
    public async Task Setting_On_And_Supported_Deck_Runs_Locally_Not_Server()
    {
        var vm = BuildVm(settingOn: true, TextDeck);

        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.ErrorMessage.Should().BeNull();
        vm.Result.Should().NotBeNull();
        var path = vm.Result!.LocalFilePath;
        path.Should().NotBeNull();
        File.Exists(path).Should().BeTrue();

        // A real, reopenable deck landed on-device — no chat-edge round trip.
        using (var doc = PresentationDocument.Open(path!, isEditable: false))
            doc.PresentationPart!.SlideParts.Should().HaveCount(2);
        _api.Verify(a => a.ExecuteDocumentPlanAsync(
                It.IsAny<string>(), It.IsAny<ExecuteDocumentPlanRequest>(), It.IsAny<CancellationToken>()),
            Times.Never);

        try { Directory.Delete(Path.GetDirectoryName(path!)!, recursive: true); }
        catch (IOException) { }
    }

    [Fact]
    public async Task Chart_Deck_Falls_Back_To_Server()
    {
        var vm = BuildVm(settingOn: true, ChartDeck);

        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.Result.Should().NotBeNull();
        vm.Result!.LocalFilePath.Should().BeNull(); // server file, not local
        vm.Result.Changes.Should().Contain("server rendered the deck");
        _api.Verify(a => a.ExecuteDocumentPlanAsync(
                "conv-1",
                It.Is<ExecuteDocumentPlanRequest>(r => r.Format == "pptx"),
                It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task Setting_Off_Uses_Server_Even_When_Locally_Supported()
    {
        var vm = BuildVm(settingOn: false, TextDeck);

        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.Result.Should().NotBeNull();
        vm.Result!.LocalFilePath.Should().BeNull();
        _api.Verify(a => a.ExecuteDocumentPlanAsync(
                "conv-1",
                It.Is<ExecuteDocumentPlanRequest>(r => r.Format == "pptx"),
                It.IsAny<CancellationToken>()),
            Times.Once);
    }
}
