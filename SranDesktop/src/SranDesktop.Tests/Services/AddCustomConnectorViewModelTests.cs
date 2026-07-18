using FluentAssertions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Custom-connector UI, form logic: can-add gating per transport and
/// auth mode, the stdio consent gate, and the small parsing helpers.</summary>
public sealed class AddCustomConnectorViewModelTests
{
    private readonly Mock<IMcpConnectorService> _connectors = new();
    private readonly Mock<IDialogService> _dialogs = new();

    private AddCustomConnectorViewModel CreateVm() =>
        new(_connectors.Object, _dialogs.Object);

    // ── can-add gating ────────────────────────────────────────────────────────

    [Fact]
    public void Empty_Form_Requires_A_Name()
    {
        var vm = CreateVm();
        vm.CanAdd.Should().BeFalse();
        vm.FirstErrorKey().Should().Be("connector.err.nameRequired");
    }

    [Fact]
    public void Http_Form_Walks_Through_Url_Rules()
    {
        var vm = CreateVm();
        vm.Name = "My Server";

        vm.FirstErrorKey().Should().Be("connector.err.urlRequired");

        vm.ServerUrl = "not a url";
        vm.FirstErrorKey().Should().Be("connector.err.urlInvalid");

        vm.ServerUrl = "http://example.com/mcp";
        vm.FirstErrorKey().Should().Be("connector.err.urlHttps");

        vm.ServerUrl = "http://localhost:3000";
        vm.FirstErrorKey().Should().BeNull();
        vm.CanAdd.Should().BeTrue();

        vm.ServerUrl = "https://example.com/mcp";
        vm.CanAdd.Should().BeTrue();
    }

    [Fact]
    public void Duplicate_Name_Among_Installed_Blocks_Add()
    {
        _connectors.Setup(c => c.IsInstalled("custom-my-server")).Returns(true);
        var vm = CreateVm();
        vm.Name = "My Server";
        vm.ServerUrl = "https://example.com/mcp";

        vm.FirstErrorKey().Should().Be("connector.err.nameTaken");
        vm.CanAdd.Should().BeFalse();
    }

    [Fact]
    public void Api_Key_Auth_Requires_Header_And_Key()
    {
        var vm = CreateVm();
        vm.Name = "My Server";
        vm.ServerUrl = "https://example.com/mcp";
        vm.UseApiKey = true;

        vm.FirstErrorKey().Should().Be("connector.err.keyRequired");

        vm.HeaderName = "";
        vm.FirstErrorKey().Should().Be("connector.err.headerRequired");

        vm.HeaderName = "X-Api-Key";
        vm.ApiKey = "sk-123";
        vm.CanAdd.Should().BeTrue();
    }

    [Fact]
    public void Stdio_Form_Requires_A_Command_And_Valid_Env()
    {
        var vm = CreateVm();
        vm.Name = "Local";
        vm.IsStdio = true;

        vm.FirstErrorKey().Should().Be("connector.err.commandRequired");

        vm.Command = "npx";
        vm.CanAdd.Should().BeTrue();

        vm.EnvLines = "NOT-AN-ASSIGNMENT";
        vm.FirstErrorKey().Should().Be("connector.err.envInvalid");

        vm.EnvLines = "API_TOKEN=abc\n\nDEBUG=1";
        vm.CanAdd.Should().BeTrue();
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    [Fact]
    public void MakeId_Slugs_The_Name_With_A_Custom_Prefix()
    {
        AddCustomConnectorViewModel.MakeId("My Server!").Should().Be("custom-my-server");
        AddCustomConnectorViewModel.MakeId("  a  b  ").Should().Be("custom-a-b");
        AddCustomConnectorViewModel.MakeId("!!!").Should().Be("custom-connector");
    }

    [Fact]
    public void SplitArguments_Honors_Quotes()
    {
        AddCustomConnectorViewModel
            .SplitArguments("-y @modelcontextprotocol/server-filesystem \"C:\\My Data\"")
            .Should().Equal("-y", "@modelcontextprotocol/server-filesystem", "C:\\My Data");
        AddCustomConnectorViewModel.SplitArguments("   ").Should().BeEmpty();
    }

    [Fact]
    public void TryParseEnv_Builds_A_Dictionary_And_Flags_Bad_Lines()
    {
        AddCustomConnectorViewModel.TryParseEnv("A=1\nB=x=y\n", out var env).Should().BeNull();
        env.Should().Equal(new Dictionary<string, string> { ["A"] = "1", ["B"] = "x=y" });

        AddCustomConnectorViewModel.TryParseEnv("=nokey", out _)
            .Should().Be("connector.err.envInvalid");

        AddCustomConnectorViewModel.TryParseEnv("  ", out var empty).Should().BeNull();
        empty.Should().BeNull();
    }

    // ── definition building ───────────────────────────────────────────────────

    [Fact]
    public void BuildConnector_Keeps_The_Api_Key_OUT_Of_The_Definition()
    {
        var vm = CreateVm();
        vm.Name = "My Server";
        vm.ServerUrl = "https://example.com/mcp";
        vm.UseApiKey = true;
        vm.HeaderName = "X-Api-Key";
        vm.ApiKey = "sk-secret";

        var connector = vm.BuildConnector();

        connector.Id.Should().Be("custom-my-server");
        connector.AuthType.Should().Be("api_key");
        connector.AuthHeader.Should().Be("X-Api-Key");
        System.Text.Json.JsonSerializer.Serialize(connector).Should().NotContain("sk-secret");
    }

    [Fact]
    public void BuildConnector_Default_Authorization_Header_Stays_Null()
    {
        var vm = CreateVm();
        vm.Name = "My Server";
        vm.ServerUrl = "https://example.com/mcp";
        vm.UseApiKey = true;
        vm.ApiKey = "sk-secret";

        vm.BuildConnector().AuthHeader.Should().BeNull("Authorization/Bearer is the default");
    }

    [Fact]
    public void BuildConnector_Stdio_Carries_Command_Args_And_Env()
    {
        var vm = CreateVm();
        vm.Name = "Local FS";
        vm.IsStdio = true;
        vm.Command = "npx";
        vm.ArgumentsLine = "-y server-fs \"C:\\data dir\"";
        vm.EnvLines = "DEBUG=1";

        var connector = vm.BuildConnector();

        connector.Transport.Should().Be(McpConnector.TransportStdio);
        connector.Command.Should().Be("npx");
        connector.Args.Should().Equal("-y", "server-fs", "C:\\data dir");
        connector.Env.Should().ContainKey("DEBUG");
        connector.ServerUrl.Should().BeEmpty();
    }

    // ── stdio consent gate ────────────────────────────────────────────────────

    [Fact]
    public async Task Declining_The_Stdio_Warning_Blocks_Install()
    {
        _dialogs.Setup(d => d.ConfirmAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()))
            .ReturnsAsync(false);
        var vm = CreateVm();
        vm.Name = "Local";
        vm.IsStdio = true;
        vm.Command = "npx";

        await vm.AddCommand.ExecuteAsync(null);

        _connectors.Verify(c => c.InstallCustomAsync(
            It.IsAny<McpConnector>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Accepting_The_Stdio_Warning_Installs_And_Closes()
    {
        _dialogs.Setup(d => d.ConfirmAsync(It.IsAny<string>(), It.Is<string>(m => m.Contains("npx -y pkg")), It.IsAny<bool>()))
            .ReturnsAsync(true);
        var vm = CreateVm();
        vm.Name = "Local";
        vm.IsStdio = true;
        vm.Command = "npx";
        vm.ArgumentsLine = "-y pkg";
        var closed = false;
        vm.RequestClose += (_, added) => closed = added;

        await vm.AddCommand.ExecuteAsync(null);

        _connectors.Verify(c => c.InstallCustomAsync(
            It.Is<McpConnector>(m => m.Id == "custom-local"), null, It.IsAny<CancellationToken>()), Times.Once);
        closed.Should().BeTrue();
    }

    [Fact]
    public async Task Http_Add_Skips_The_Stdio_Warning_And_Passes_The_Key()
    {
        var vm = CreateVm();
        vm.Name = "My Server";
        vm.ServerUrl = "https://example.com/mcp";
        vm.UseApiKey = true;
        vm.ApiKey = "sk-123";

        await vm.AddCommand.ExecuteAsync(null);

        _dialogs.Verify(d => d.ConfirmAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()), Times.Never);
        _connectors.Verify(c => c.InstallCustomAsync(
            It.IsAny<McpConnector>(), "sk-123", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Test_Connection_Shows_The_Tool_Count_Or_The_Error()
    {
        _connectors.Setup(c => c.TestConnectionAsync(
                It.IsAny<McpConnector>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectorTestResult.Ok(7));
        var vm = CreateVm();
        vm.Name = "My Server";
        vm.ServerUrl = "https://example.com/mcp";

        await vm.TestConnectionCommand.ExecuteAsync(null);

        vm.TestFailed.Should().BeFalse();
        vm.TestStatusText.Should().Contain("7");

        _connectors.Setup(c => c.TestConnectionAsync(
                It.IsAny<McpConnector>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectorTestResult.Fail("connection refused"));

        await vm.TestConnectionCommand.ExecuteAsync(null);

        vm.TestFailed.Should().BeTrue();
        vm.TestStatusText.Should().Be("connection refused");
    }
}
