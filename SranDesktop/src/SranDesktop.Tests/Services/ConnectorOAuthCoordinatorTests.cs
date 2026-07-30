using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 7 C3: the broker connect flow — ticket → browser →
/// deep-link callback → tokenless install. No InputDialog anywhere.</summary>
public class ConnectorOAuthCoordinatorTests
{
    private readonly Mock<IConnectorAuthClient> _auth = new();
    private readonly Mock<IMcpConnectorService> _connectors = new();
    private readonly Mock<IBrowserLauncher> _browser = new();

    private ConnectorOAuthCoordinator Create() => new(
        _auth.Object, _connectors.Object, _browser.Object,
        NullLogger<ConnectorOAuthCoordinator>.Instance);

    private void SetupConnections(params (string Id, bool Connected)[] rows)
    {
        _auth.Setup(a => a.GetConnectionsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(rows.Select(r => new ConnectorConnection
            {
                ConnectorId = r.Id, Connected = r.Connected, Provider = "x"
            }).ToList());
    }

    // ── StartConnect ────────────────────────────────────────────────────────

    [Fact]
    public async Task StartConnect_Mints_Ticket_And_Opens_Browser()
    {
        _auth.Setup(a => a.CreateTicketAsync("slack", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConnectorTicket
            {
                Ticket = "t1",
                StartUrl = "http://gw/portal/connectors/oauth/start?ticket=t1"
            });
        _browser.Setup(b => b.Open(It.IsAny<string>())).Returns(true);
        var coord = Create();

        var ok = await coord.StartConnectAsync("slack");

        ok.Should().BeTrue();
        _browser.Verify(
            b => b.Open("http://gw/portal/connectors/oauth/start?ticket=t1"),
            Times.Once);
        coord.GetState("slack").Should().Be(ConnectorFlowState.Connecting);
    }

    [Fact]
    public async Task StartConnect_Ticket_Failure_Is_Error_State()
    {
        _auth.Setup(a => a.CreateTicketAsync("slack", It.IsAny<CancellationToken>()))
            .ReturnsAsync((ConnectorTicket?)null);
        var coord = Create();

        (await coord.StartConnectAsync("slack")).Should().BeFalse();

        coord.GetState("slack").Should().Be(ConnectorFlowState.Error);
        coord.GetErrorReason("slack").Should().Be("ticket_failed");
        _browser.Verify(b => b.Open(It.IsAny<string>()), Times.Never);
    }

    // ── Callback ────────────────────────────────────────────────────────────

    [Fact]
    public async Task Success_Callback_Installs_Without_Any_Secret()
    {
        SetupConnections(("slack", true));
        var coord = Create();

        await coord.HandleCallbackAsync(new OAuthCallbackResult("slack", true, null));

        // The key Phase 7 assertion: apiToken is NULL, authMode is oauth2.
        _connectors.Verify(c => c.InstallAsync(
            "slack", null, InstalledConnector.AuthModeOAuth2,
            It.IsAny<UserApprovalEvidence?>(), It.IsAny<CancellationToken>()), Times.Once);
        _connectors.Verify(c => c.PingAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
        coord.GetState("slack").Should().Be(ConnectorFlowState.Connected);
    }

    [Fact]
    public async Task Success_Claim_Is_Verified_With_The_Backend()
    {
        // A local attacker can fire sran://…status=ok at us — the backend
        // listing is the source of truth, so an unconfirmed "ok" is an error.
        SetupConnections(); // backend says: nothing connected
        var coord = Create();

        await coord.HandleCallbackAsync(new OAuthCallbackResult("slack", true, null));

        _connectors.Verify(c => c.InstallAsync(
                It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<UserApprovalEvidence?>(), It.IsAny<CancellationToken>()),
            Times.Never);
        coord.GetState("slack").Should().Be(ConnectorFlowState.Error);
        coord.GetErrorReason("slack").Should().Be("not_confirmed");
    }

    [Fact]
    public async Task Error_Callback_Surfaces_Coarse_Reason()
    {
        var coord = Create();

        await coord.HandleCallbackAsync(
            new OAuthCallbackResult("gmail", false, "provider_denied"));

        coord.GetState("gmail").Should().Be(ConnectorFlowState.Error);
        coord.GetErrorReason("gmail").Should().Be("provider_denied");
        _auth.Verify(a => a.GetConnectionsAsync(It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public void Malformed_Deep_Link_Is_Dropped()
    {
        var coord = Create();

        coord.HandleDeepLink("sran://oauth/steal?connector=slack&status=ok");
        coord.HandleDeepLink("total garbage");

        _auth.Verify(a => a.GetConnectionsAsync(It.IsAny<CancellationToken>()), Times.Never);
        coord.GetState("slack").Should().Be(ConnectorFlowState.NotConnected);
    }

    // ── Disconnect ──────────────────────────────────────────────────────────

    [Fact]
    public async Task Disconnect_Uninstalls_Locally_And_Best_Effort_Server_Side()
    {
        _auth.Setup(a => a.DisconnectAsync("slack", It.IsAny<CancellationToken>()))
            .ReturnsAsync(true);
        var coord = Create();

        await coord.DisconnectAsync("slack");

        _connectors.Verify(c => c.UninstallAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
        _auth.Verify(a => a.DisconnectAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
        coord.GetState("slack").Should().Be(ConnectorFlowState.NotConnected);
    }

    [Fact]
    public async Task Disconnect_Survives_Server_Failure()
    {
        _auth.Setup(a => a.DisconnectAsync("slack", It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("gateway down"));
        var coord = Create();

        await coord.DisconnectAsync("slack"); // must not throw

        _connectors.Verify(c => c.UninstallAsync("slack", It.IsAny<CancellationToken>()), Times.Once);
        coord.GetState("slack").Should().Be(ConnectorFlowState.NotConnected);
    }
}
