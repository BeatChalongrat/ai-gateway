using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Custom-connector UI, service layer: definition validation,
/// duplicate rejection, DPAPI-only api keys, and the portal-JWT guard for
/// user-added auth-none servers.</summary>
public sealed class CustomConnectorInstallTests : IDisposable
{
    private readonly string _stateFile = Path.Combine(
        Path.GetTempPath(), $"sran-custom-{Guid.NewGuid():N}.json");
    private readonly Mock<ICredentialStore> _credentials = new();
    private readonly Mock<ITokenSession> _tokenSession = new();

    public CustomConnectorInstallTests() =>
        _tokenSession.SetupGet(t => t.AccessToken).Returns("portal-jwt");

    private McpConnectorService CreateService() => new(
        _credentials.Object, NullLogger<McpConnectorService>.Instance,
        tokenSession: _tokenSession.Object, stateFilePath: _stateFile,
        dialTimeout: TimeSpan.FromSeconds(5));

    /// <summary>Loopback URL that refuses instantly — installs complete fast
    /// with status Disconnected, which is all these tests need.</summary>
    private static McpConnector HttpConnector(string id = "custom-my-server", string authType = "none") => new()
    {
        Id = id,
        Name = "My Server",
        Transport = McpConnector.TransportHttp,
        ServerUrl = "http://127.0.0.1:1",
        AuthType = authType
    };

    // ── definition validation ─────────────────────────────────────────────────

    [Fact]
    public void Validate_Rejects_Stdio_Without_A_Command()
    {
        var act = () => McpConnectorService.ValidateCustomDefinition(new McpConnector
        {
            Id = "x", Name = "X", Transport = McpConnector.TransportStdio
        });
        act.Should().Throw<ArgumentException>().WithMessage("*command*");
    }

    [Theory]
    [InlineData("")]
    [InlineData("not a url")]
    [InlineData("ftp://example.com/mcp")]
    [InlineData("file:///C:/x")]
    public void Validate_Rejects_Bad_Http_Urls(string url)
    {
        var act = () => McpConnectorService.ValidateCustomDefinition(new McpConnector
        {
            Id = "x", Name = "X", ServerUrl = url
        });
        act.Should().Throw<ArgumentException>();
    }

    [Fact]
    public void Validate_Requires_Https_For_Remote_Hosts_But_Allows_Http_Loopback()
    {
        var remote = () => McpConnectorService.ValidateCustomDefinition(new McpConnector
        {
            Id = "x", Name = "X", ServerUrl = "http://example.com/mcp"
        });
        remote.Should().Throw<ArgumentException>().WithMessage("*https*");

        var loopback = () => McpConnectorService.ValidateCustomDefinition(new McpConnector
        {
            Id = "x", Name = "X", ServerUrl = "http://localhost:3000"
        });
        loopback.Should().NotThrow();

        var https = () => McpConnectorService.ValidateCustomDefinition(new McpConnector
        {
            Id = "x", Name = "X", ServerUrl = "https://example.com/mcp"
        });
        https.Should().NotThrow();
    }

    // ── install behavior ──────────────────────────────────────────────────────

    [Fact]
    public async Task Install_Rejects_A_Duplicate_Id()
    {
        await using var svc = CreateService();
        await svc.InstallCustomAsync(HttpConnector());

        var act = () => svc.InstallCustomAsync(HttpConnector());
        await act.Should().ThrowAsync<ArgumentException>().WithMessage("*already installed*");
    }

    [Fact]
    public async Task Api_Key_Goes_To_Dpapi_And_Never_Into_Connectors_Json()
    {
        await using var svc = CreateService();
        await svc.InstallCustomAsync(HttpConnector(authType: "api_key"), "sk-secret-xyz");

        _credentials.Verify(c => c.Save("connector:custom-my-server", "sk-secret-xyz"), Times.Once);

        var json = File.ReadAllText(_stateFile);
        json.Should().NotContain("sk-secret-xyz");

        var record = JsonSerializer.Deserialize<List<InstalledConnector>>(json)!.Single();
        record.HasCredentials.Should().BeTrue();
        record.AuthMode.Should().Be(InstalledConnector.AuthModeApiKey);
        record.Custom.Should().NotBeNull();
    }

    [Fact]
    public async Task Custom_Auth_None_Connector_Never_Receives_The_Portal_Jwt()
    {
        await using var svc = CreateService();
        var connector = HttpConnector(authType: "none");
        await svc.InstallCustomAsync(connector);

        // A CATALOG auth-none connector (built-in gateway server) does get the
        // live portal JWT — a USER-ADDED one is an arbitrary URL and must not.
        svc.ResolveTokenProvider(connector)().Should().BeNull();

        var catalog = new McpConnector { Id = "not-custom", Name = "Catalog", AuthType = "none" };
        svc.ResolveTokenProvider(catalog)().Should().Be("portal-jwt");
    }

    [Fact]
    public async Task Test_Connection_Persists_Nothing_And_Reports_The_Failure_Inline()
    {
        await using var svc = CreateService();

        var result = await svc.TestConnectionAsync(HttpConnector(), "sk-test-token");

        result.Success.Should().BeFalse();
        result.Error.Should().NotBeNullOrEmpty();
        File.Exists(_stateFile).Should().BeFalse("test connection must not persist state");
        _credentials.Verify(c => c.Save(It.IsAny<string>(), It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public async Task Test_Connection_FailsClosed_For_Stdio_Without_Persisting()
    {
        await using var svc = CreateService();
        var (command, args) = Mcp.StdioEchoServer.CommandLine();

        var result = await svc.TestConnectionAsync(new McpConnector
        {
            Id = "custom-echo-test",
            Name = "Echo Test",
            Transport = McpConnector.TransportStdio,
            Command = command,
            Args = args
        }).WaitAsync(TimeSpan.FromSeconds(30));

        result.Success.Should().BeFalse();
        // "Just to test it" runs the same code as running it, so an unapproved
        // local executable is refused before anything is spawned.
        result.Error.Should().Contain("approved");
        result.ToolCount.Should().Be(0);
        File.Exists(_stateFile).Should().BeFalse("test connection must not persist state");
        svc.IsInstalled("custom-echo-test").Should().BeFalse();
    }

    [Fact]
    public async Task Test_Connection_Refuses_To_Dial_Without_Evidence_Of_A_Person()
    {
        await using var svc = CreateService();

        // No UserApprovalEvidence: this could be the agent asking.
        var result = await svc.TestConnectionAsync(HttpConnector());

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("started by a person");
        File.Exists(_stateFile).Should().BeFalse();
    }

    [Fact]
    public async Task Stdio_Definition_With_Environment_Is_Rejected_Without_Persistence()
    {
        await using var svc = CreateService();
        var connector = new McpConnector
        {
            Id = "stdio-with-env",
            Name = "Local",
            Transport = McpConnector.TransportStdio,
            Command = "server.exe",
            Env = new Dictionary<string, string>
            {
                ["API_TOKEN"] = "ghp_1234567890abcdefghijklmnop"
            }
        };

        var action = () => svc.InstallCustomAsync(connector);

        await action.Should().ThrowAsync<ArgumentException>()
            .WithMessage("*environment values are not accepted*");
        File.Exists(_stateFile).Should().BeFalse();
    }

    [Fact]
    public async Task Stdio_Definition_With_Secret_Argument_Is_Rejected_Without_Persistence()
    {
        await using var svc = CreateService();
        var connector = new McpConnector
        {
            Id = "stdio-with-arg-secret",
            Name = "Local",
            Transport = McpConnector.TransportStdio,
            Command = "server.exe",
            Args = ["--token", "ghp_1234567890abcdefghijklmnop"]
        };

        var action = () => svc.InstallCustomAsync(connector);

        await action.Should().ThrowAsync<ArgumentException>()
            .WithMessage("*arguments contain sensitive data*");
        File.Exists(_stateFile).Should().BeFalse();
    }

    [Fact]
    public async Task Test_Connection_Reports_An_Invalid_Definition_As_Failure_Not_Throw()
    {
        await using var svc = CreateService();

        var result = await svc.TestConnectionAsync(new McpConnector
        {
            Id = "x", Name = "X", ServerUrl = "http://example.com/mcp"
        });

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("https");
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
