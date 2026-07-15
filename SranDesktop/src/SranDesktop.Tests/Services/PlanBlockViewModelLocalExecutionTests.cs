using System.Text.Json;
using ClosedXML.Excel;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Office;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 12 E: PlanBlockViewModel routing between the on-device
/// executor and the chat-edge server path — setting gate, CanExecute gate,
/// and transparent fallback when local execution fails.</summary>
public class PlanBlockViewModelLocalExecutionTests
{
    private static readonly string ExcelPlanJson = """
    {
      "operation": "create_workbook",
      "output_filename": "report.xlsx",
      "actions": [
        {"type": "create_sheet", "sheet_name": "Data"},
        {"type": "write_cells", "sheet_name": "Data",
         "cells": [{"cell": "A1", "value": "hello"}]}
      ]
    }
    """;

    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<ILocalPlanExecutor> _local = new();
    private readonly Mock<ISettingsService> _settings = new();

    private static JsonElement Plan(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private static PlanExecutionResult ServerResult() => new(
        new PlanFileDto("srv-1", "report.xlsx", "application/octet-stream", 42,
            "/api/v1/files/srv-1/download", 1),
        new PlanValidationDto(true, null, null, null, null),
        new List<string> { "server did it" });

    private PlanBlockViewModel BuildVm(
        bool settingOn, string planType = "excel",
        ILocalPlanExecutor? localExecutor = null, string? planJson = null)
    {
        _settings.SetupGet(s => s.Current).Returns(new UserSettings
        {
            PreferLocalOfficeGeneration = settingOn,
        });
        _api.Setup(a => a.ExecuteExcelPlanAsync(
                It.IsAny<string>(), It.IsAny<ExecuteExcelPlanRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServerResult());
        _api.Setup(a => a.ExecuteDocumentPlanAsync(
                It.IsAny<string>(), It.IsAny<ExecuteDocumentPlanRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServerResult());

        return new PlanBlockViewModel(
            _api.Object, "conv-1", planType, Plan(planJson ?? ExcelPlanJson),
            localExecutor, _settings.Object, NullLogger<PlanBlockViewModel>.Instance);
    }

    [Fact]
    public async Task Setting_On_And_Supported_Runs_Locally_Not_Server()
    {
        var localPath = Path.Combine(Path.GetTempPath(), "sran-vm-local", "report.xlsx");
        _local.Setup(e => e.CanExecute(It.IsAny<JsonElement>())).Returns(true);
        _local.Setup(e => e.ExecuteAsync(
                It.IsAny<JsonElement>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new LocalPlanExecutionResult(
                localPath, 123, new List<string> { "Created sheet 'Data'" },
                new Dictionary<string, int> { ["sheets"] = 1 }));

        var vm = BuildVm(settingOn: true, localExecutor: _local.Object);
        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.Result.Should().NotBeNull();
        vm.ErrorMessage.Should().BeNull();
        vm.Result!.LocalFilePath.Should().Be(localPath);
        vm.Result.Changes.Should().ContainSingle().Which.Should().Be("Created sheet 'Data'");
        // The staged output path honors the plan's output_filename.
        _local.Verify(e => e.ExecuteAsync(
            It.IsAny<JsonElement>(), It.Is<string>(p => p.EndsWith("report.xlsx")),
            It.IsAny<CancellationToken>()), Times.Once);
        _api.Verify(a => a.ExecuteExcelPlanAsync(
                It.IsAny<string>(), It.IsAny<ExecuteExcelPlanRequest>(), It.IsAny<CancellationToken>()),
            Times.Never);
    }

    [Fact]
    public async Task Unsupported_Plan_Falls_Back_To_Server()
    {
        _local.Setup(e => e.CanExecute(It.IsAny<JsonElement>())).Returns(false);

        var vm = BuildVm(settingOn: true, localExecutor: _local.Object);
        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.Result.Should().NotBeNull();
        vm.Result!.LocalFilePath.Should().BeNull();
        _local.Verify(e => e.ExecuteAsync(
                It.IsAny<JsonElement>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        _api.Verify(a => a.ExecuteExcelPlanAsync(
                "conv-1", It.IsAny<ExecuteExcelPlanRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task Setting_Off_Uses_Server_Even_When_Locally_Supported()
    {
        _local.Setup(e => e.CanExecute(It.IsAny<JsonElement>())).Returns(true);

        var vm = BuildVm(settingOn: false, localExecutor: _local.Object);
        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.Result.Should().NotBeNull();
        vm.Result!.LocalFilePath.Should().BeNull();
        _local.Verify(e => e.ExecuteAsync(
                It.IsAny<JsonElement>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        _api.Verify(a => a.ExecuteExcelPlanAsync(
                "conv-1", It.IsAny<ExecuteExcelPlanRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task Local_Failure_Falls_Back_To_Server_Transparently()
    {
        _local.Setup(e => e.CanExecute(It.IsAny<JsonElement>())).Returns(true);
        _local.Setup(e => e.ExecuteAsync(
                It.IsAny<JsonElement>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new IOException("disk full"));

        var vm = BuildVm(settingOn: true, localExecutor: _local.Object);
        await vm.ExecuteCommand.ExecuteAsync(null);

        // The click still succeeds — server result, no surfaced error.
        vm.ErrorMessage.Should().BeNull();
        vm.Result.Should().NotBeNull();
        vm.Result!.LocalFilePath.Should().BeNull();
        vm.Result.Changes.Should().Contain("server did it");
        _api.Verify(a => a.ExecuteExcelPlanAsync(
                "conv-1", It.IsAny<ExecuteExcelPlanRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task Pptx_Has_No_Local_Executor_And_Uses_Server()
    {
        var vm = BuildVm(
            settingOn: true, planType: "pptx", localExecutor: null,
            planJson: """{"slides":[{"type":"title","title":"Deck"}]}""");
        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.Result.Should().NotBeNull();
        _api.Verify(a => a.ExecuteDocumentPlanAsync(
                "conv-1",
                It.Is<ExecuteDocumentPlanRequest>(r => r.Format == "pptx"),
                It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task EndToEnd_Real_Excel_Executor_Produces_Openable_File()
    {
        var vm = BuildVm(settingOn: true, localExecutor: new LocalExcelPlanExecutor());
        await vm.ExecuteCommand.ExecuteAsync(null);

        vm.ErrorMessage.Should().BeNull();
        vm.Result.Should().NotBeNull();
        var path = vm.Result!.LocalFilePath;
        path.Should().NotBeNull();
        File.Exists(path).Should().BeTrue();
        using var wb = new XLWorkbook(path!);
        wb.Worksheet("Data").Cell("A1").GetString().Should().Be("hello");
        _api.Verify(a => a.ExecuteExcelPlanAsync(
                It.IsAny<string>(), It.IsAny<ExecuteExcelPlanRequest>(), It.IsAny<CancellationToken>()),
            Times.Never);

        // Clean up the staged temp file (its unique parent dir).
        try { Directory.Delete(Path.GetDirectoryName(path!)!, recursive: true); }
        catch (IOException) { }
    }
}
