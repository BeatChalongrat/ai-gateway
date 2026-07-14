using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 7 C6: the card's oauth2 toggle goes through the broker
/// coordinator (ticket → injected browser launcher) — no InputDialog, no
/// DPAPI write. Headless: an InputDialog would NRE on the missing
/// MainWindow, so simply completing is itself part of the assertion.</summary>
public class ConnectorMarketplaceOAuthTests
{
    private readonly Mock<IConnectorAuthClient> _auth = new();
    private readonly Mock<IMcpConnectorService> _connectors = new();
    private readonly Mock<IBrowserLauncher> _browser = new();

    private ConnectorItemViewModel CreateItem(string authType, bool installed = false)
    {
        _connectors.Setup(c => c.IsInstalled("slack")).Returns(installed);
        var coordinator = new ConnectorOAuthCoordinator(
            _auth.Object, _connectors.Object, _browser.Object,
            NullLogger<ConnectorOAuthCoordinator>.Instance);
        var connector = new McpConnector
        {
            Id = "slack", Name = "Slack", AuthType = authType,
            Category = "communication", ServerUrl = "https://gw/mcp/servers/slack",
            Tools = new List<string> { "slack_post_message" }
        };
        return new ConnectorItemViewModel(_connectors.Object, coordinator, connector);
    }

    [Fact]
    public async Task OAuth2_Toggle_Opens_Browser_Via_Ticket_No_Dialog()
    {
        _auth.Setup(a => a.CreateTicketAsync("slack", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConnectorTicket { Ticket = "t", StartUrl = "http://gw/start?ticket=t" });
        _browser.Setup(b => b.Open(It.IsAny<string>())).Returns(true);
        var item = CreateItem("oauth2");

        await item.ToggleInstallCommand.ExecuteAsync(null);

        _browser.Verify(b => b.Open("http://gw/start?ticket=t"), Times.Once);
        // Nothing installed yet, and definitely no pasted token stored.
        _connectors.Verify(c => c.InstallAsync(
                It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<CancellationToken>()),
            Times.Never);
        item.FlowState.Should().Be(ConnectorFlowState.Connecting);
        item.IsInstalled.Should().BeFalse();
    }

    [Fact]
    public async Task OAuth2_Toggle_While_Connecting_Is_A_NoOp()
    {
        _auth.Setup(a => a.CreateTicketAsync("slack", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConnectorTicket { Ticket = "t", StartUrl = "http://gw/start?ticket=t" });
        _browser.Setup(b => b.Open(It.IsAny<string>())).Returns(true);
        var item = CreateItem("oauth2");

        await item.ToggleInstallCommand.ExecuteAsync(null);
        await item.ToggleInstallCommand.ExecuteAsync(null); // second click mid-flight

        _auth.Verify(a => a.CreateTicketAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task OAuth2_Connected_Toggle_Disconnects_Server_Side_Too()
    {
        // Model the real service contract: after UninstallAsync the
        // connector no longer reports installed.
        _connectors.Setup(c => c.UninstallAsync("slack", It.IsAny<CancellationToken>()))
            .Callback(() => _connectors.Setup(c => c.IsInstalled("slack")).Returns(false))
            .Returns(Task.CompletedTask);
        var item = CreateItem("oauth2", installed: true);

        await item.ToggleInstallCommand.ExecuteAsync(null);

        _connectors.Verify(c => c.UninstallAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
        _auth.Verify(a => a.DisconnectAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
        item.IsInstalled.Should().BeFalse();
    }
}

/// <summary>Phase 7 C4: bearer-source routing by install mode.</summary>
public sealed class McpConnectorTokenRoutingTests : IDisposable
{
    private readonly string _stateFile = Path.Combine(
        Path.GetTempPath(), $"sran-conn-{Guid.NewGuid():N}.json");
    private readonly Mock<ICredentialStore> _credentials = new();
    private readonly Mock<ITokenSession> _session = new();

    private McpConnectorService CreateService() => new(
        _credentials.Object, NullLogger<McpConnectorService>.Instance,
        catalogUrl: null, tokenSession: _session.Object, stateFilePath: _stateFile);

    private void SeedState(params object[] records) =>
        File.WriteAllText(_stateFile, JsonSerializer.Serialize(records));

    [Fact]
    public void OAuth2_Install_Uses_Live_Portal_Jwt()
    {
        SeedState(new { id = "slack", auth_mode = "oauth2", has_credentials = false });
        var jwt = "jwt-1";
        _session.SetupGet(s => s.AccessToken).Returns(() => jwt);
        var svc = CreateService();

        var provider = svc.ResolveTokenProvider("slack");

        provider().Should().Be("jwt-1");
        jwt = "jwt-2"; // session refreshed mid-conversation
        provider().Should().Be("jwt-2", "oauth2 resolves the token PER CALL");
        _credentials.Verify(c => c.Load(It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public void ApiKey_Install_Uses_Dpapi_Token()
    {
        SeedState(new { id = "notion", auth_mode = "api_key", has_credentials = true });
        _credentials.Setup(c => c.Load("connector:notion")).Returns("napi-key");
        var svc = CreateService();

        svc.ResolveTokenProvider("notion")().Should().Be("napi-key");
        _session.VerifyGet(s => s.AccessToken, Times.Never);
    }

    [Fact]
    public void Legacy_Record_Without_AuthMode_Stays_On_Dpapi()
    {
        SeedState(new { id = "jira", has_credentials = true }); // pre-Phase-7 shape
        _credentials.Setup(c => c.Load("connector:jira")).Returns("legacy-key");
        var svc = CreateService();

        svc.ResolveTokenProvider("jira")().Should().Be("legacy-key");
    }

    [Fact]
    public async Task OAuth2_Install_Never_Writes_Dpapi()
    {
        var svc = CreateService();

        // "zz-test" is not in any catalog, so the post-install dial is a
        // NotConfigured no-op — no network in this test.
        await svc.InstallAsync("zz-test", apiToken: null,
            authMode: InstalledConnector.AuthModeOAuth2);

        _credentials.Verify(c => c.Save(It.IsAny<string>(), It.IsAny<string>()), Times.Never);
        var state = JsonSerializer.Deserialize<List<InstalledConnector>>(
            File.ReadAllText(_stateFile))!;
        state.Single().AuthMode.Should().Be(InstalledConnector.AuthModeOAuth2);
        state.Single().HasCredentials.Should().BeFalse();
    }

    [Fact]
    public void NoneAuth_BuiltIn_Sends_Live_Portal_Jwt()
    {
        // Phase 10 C1: the mcp-gateway now REQUIRES a portal access JWT on
        // built-in (auth_type "none") servers and scopes filesystem/memory
        // per user — so the desktop must send the live token on them.
        SeedState(new { id = "filesystem", has_credentials = false });
        var jwt = "jwt-1";
        _session.SetupGet(s => s.AccessToken).Returns(() => jwt);
        var svc = CreateService();

        var provider = svc.ResolveTokenProvider(new McpConnector
        {
            Id = "filesystem",
            AuthType = "none",
            ServerUrl = "http://localhost:3200/mcp/servers/filesystem"
        });

        provider().Should().Be("jwt-1");
        jwt = "jwt-2"; // session refreshed mid-conversation
        provider().Should().Be("jwt-2", "none-auth resolves the token PER CALL");
        _credentials.Verify(c => c.Load(It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public void ApiKey_Connector_Does_Not_Fall_Back_To_Portal_Jwt()
    {
        // Guard the C1 scoping: only auth_type "none" may carry the portal
        // JWT — an api_key connector with a lost credential must keep
        // sending nothing rather than leak the portal token to a
        // third-party server URL.
        SeedState(new { id = "notion", auth_mode = "api_key", has_credentials = true });
        _credentials.Setup(c => c.Load("connector:notion")).Returns((string?)null);
        _session.SetupGet(s => s.AccessToken).Returns("portal-jwt");
        var svc = CreateService();

        var provider = svc.ResolveTokenProvider(new McpConnector
        {
            Id = "notion",
            AuthType = "api_key",
            ServerUrl = "https://mcp.sran.ai/notion"
        });

        provider().Should().BeNull();
    }

    [Fact]
    public async Task ApiKey_Install_Still_Writes_Dpapi()
    {
        var svc = CreateService();

        await svc.InstallAsync("zz-key", apiToken: "pasted-token");

        _credentials.Verify(c => c.Save("connector:zz-key", "pasted-token"), Times.Once);
        var state = JsonSerializer.Deserialize<List<InstalledConnector>>(
            File.ReadAllText(_stateFile))!;
        state.Single().AuthMode.Should().Be(InstalledConnector.AuthModeApiKey);
    }

    public void Dispose()
    {
        try
        {
            if (File.Exists(_stateFile)) File.Delete(_stateFile);
        }
        catch (IOException) { }
    }
}
