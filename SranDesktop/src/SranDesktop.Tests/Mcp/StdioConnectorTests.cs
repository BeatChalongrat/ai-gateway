using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Legacy connectors.json compatibility, and what a user-added connector is
/// allowed to be.
///
/// Phase 4.1C narrowed this deliberately. A pasted command line used to be a way
/// to get a local executable launched; it carried no manifest, so there was
/// nothing to show at approval time and nothing to hash. Local servers now come
/// as manifest-bearing packages in the managed folder, and everything reached
/// over the wire keeps working exactly as it did.
/// </summary>
public sealed class McpTransportSelectionTests : IDisposable
{
    private readonly string _stateFile = Path.Combine(
        Path.GetTempPath(), $"sran-sel-{Guid.NewGuid():N}.json");

    private McpConnectorService CreateService() => new(
        Mock.Of<ICredentialStore>(), NullLogger<McpConnectorService>.Instance,
        stateFilePath: _stateFile);

    [Fact]
    public async Task A_Pasted_Command_Line_Is_Not_A_Way_To_Run_A_Local_Server()
    {
        await using var service = CreateService();

        var add = () => service.InstallCustomAsync(new McpConnector
        {
            Id = "local",
            Name = "Local",
            Transport = McpConnector.TransportStdio,
            Command = "server.exe",
            Args = ["--flag"]
        });

        await add.Should().ThrowAsync<ArgumentException>()
            .WithMessage("*managed connector folder*",
                "a local executable has to arrive as a package whose manifest says what "
                + "it wants, or the approval screen has nothing to show");
    }

    [Fact]
    public void Legacy_Connector_Json_Without_Transport_Field_Is_Still_Http()
    {
        // A REAL pre-Phase-12 catalog entry — no "transport" key existed.
        const string legacyCatalogEntry = """
            {"id":"notion","name":"Notion","description":"Search and read pages",
             "category":"productivity","auth_type":"api_key",
             "server_url":"https://mcp.sran.ai/notion",
             "tools":["notion_search","notion_read_page"]}
            """;
        var connector = JsonSerializer.Deserialize<McpConnector>(
            legacyCatalogEntry, new JsonSerializerOptions(JsonSerializerDefaults.Web))!;

        connector.Transport.Should().Be(McpConnector.TransportHttp,
            "legacy connectors must keep their exact pre-Phase-12 behavior");
        connector.ServerUrl.Should().Be("https://mcp.sran.ai/notion");

        var declaration = McpConnectorService.ToDeclaration(connector, "catalog");

        declaration.Transport.Should().Be(ConnectorTransportKind.Http);
        declaration.ServerUrl.Should().Be("https://mcp.sran.ai/notion");
        declaration.Permissions.RequiresNetwork.Should().BeTrue();
    }

    [Fact]
    public void A_Remote_Connector_Repointed_At_Another_Server_Is_A_Different_Declaration()
    {
        var original = McpConnectorService.ToDeclaration(new McpConnector
        {
            Id = "notion", Name = "Notion", ServerUrl = "https://mcp.sran.ai/notion"
        });
        var moved = McpConnectorService.ToDeclaration(new McpConnector
        {
            Id = "notion", Name = "Notion", ServerUrl = "https://evil.example/notion"
        });

        moved.EndpointFingerprint().Should().NotBe(original.EndpointFingerprint());
        moved.ComputeFingerprint("").Should().NotBe(original.ComputeFingerprint(""),
            "an approval names a server, not a label");
    }

    [Fact]
    public void Legacy_ConnectorsJson_Deserializes_And_Resaves_Without_New_Fields()
    {
        // A REAL pre-Phase-12 connectors.json: a Phase-7 record plus a
        // minimal legacy one (the shape live installs actually wrote).
        const string legacyStateJson = """
            [
              {
                "id": "notion",
                "installed_at": "2026-05-01T09:30:00+00:00",
                "has_credentials": true,
                "auth_mode": "api_key"
              },
              { "id": "jira" }
            ]
            """;

        var state = JsonSerializer.Deserialize<List<InstalledConnector>>(legacyStateJson)!;

        state.Should().HaveCount(2);
        state[0].Id.Should().Be("notion");
        state[0].HasCredentials.Should().BeTrue();
        state[0].AuthMode.Should().Be("api_key");
        state[0].InstalledAt.Should().Be(DateTimeOffset.Parse("2026-05-01T09:30:00+00:00"));
        state[1].Id.Should().Be("jira");
        state.Should().OnlyContain(r => r.Custom == null,
            "legacy records carry no custom definition");

        // Re-saving with the service's serializer options must not grow keys:
        // pre-existing installs stay byte-compatible on disk.
        var resaved = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
        resaved.Should().NotContain("custom").And.NotContain("transport");

        var again = JsonSerializer.Deserialize<List<InstalledConnector>>(resaved)!;
        again[0].InstalledAt.Should().Be(state[0].InstalledAt);
        again[0].AuthMode.Should().Be("api_key");
        again[1].AuthMode.Should().BeNull();
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

/// <summary>
/// A connector record left over from before local servers were packaged. The
/// point of every test here is that nothing about it becomes runnable by
/// accident — not by restarting, not by refreshing, not by resolving its tool
/// name — and that the secrets an old record may be carrying are scrubbed.
/// </summary>
public sealed class LegacyStdioConnectorRecordTests : IDisposable
{
    private readonly string _stateFile = Path.Combine(
        Path.GetTempPath(), $"sran-stdio-{Guid.NewGuid():N}.json");

    private McpConnectorService CreateService() => new(
        Mock.Of<ICredentialStore>(), NullLogger<McpConnectorService>.Instance,
        stateFilePath: _stateFile);

    private void SeedLegacyStdioRecord(params string[] args) =>
        File.WriteAllText(_stateFile, JsonSerializer.Serialize(new List<InstalledConnector>
        {
            new()
            {
                Id = "legacy-local",
                Custom = new McpConnector
                {
                    Id = "legacy-local",
                    Name = "Legacy Local",
                    Transport = McpConnector.TransportStdio,
                    Command = "server.exe",
                    Args = args.Length > 0 ? [.. args] : null
                }
            }
        }));

    [Fact]
    public async Task A_Legacy_Stdio_Record_Is_Never_Offered_As_A_Declaration()
    {
        SeedLegacyStdioRecord();
        await using var service = CreateService();

        service.InstalledDeclarations().Should().BeEmpty(
            "a stdio record with no package and no manifest cannot be resolved into "
            + "something a person could meaningfully approve");
    }

    [Fact]
    public async Task A_Legacy_Stdio_Record_Exposes_No_Tools_After_A_Restart()
    {
        SeedLegacyStdioRecord();

        // A new service instance is the app restarting: state comes purely from
        // connectors.json.
        await using var service = CreateService();
        await service.RefreshRemoteToolsAsync();
        await service.RefreshRemoteToolsAsync();

        service.GetStatus("legacy-local").Should().Be(ConnectorStatus.NotConfigured);
        service.GetCachedRemoteToolDefinitions().Should().BeEmpty();
    }

    [Fact]
    public async Task A_Legacy_Stdio_Tool_Name_Resolves_To_Nothing()
    {
        SeedLegacyStdioRecord();
        await using var service = CreateService();
        await service.RefreshRemoteToolsAsync();

        // The same resolution path the orchestrator uses for every remote tool.
        var host = new McpToolHost(new McpToolRegistry(), service);

        host.Resolve("stdio_echo").Should().BeNull(
            "permission consent cannot substitute for a trust decision about a binary");
    }

    [Fact]
    public async Task Legacy_Stdio_Environment_Secrets_Are_Removed_Before_State_Is_Returned()
    {
        const string secret = "ghp_1234567890abcdefghijklmnop";
        var legacy = new List<InstalledConnector>
        {
            new()
            {
                Id = "legacy-local",
                Custom = new McpConnector
                {
                    Id = "legacy-local",
                    Name = "Legacy Local",
                    Transport = McpConnector.TransportStdio,
                    Command = "server.exe",
                    Args = ["--token", secret],
                    Env = new Dictionary<string, string> { ["API_TOKEN"] = secret }
                }
            }
        };
        await File.WriteAllTextAsync(_stateFile, JsonSerializer.Serialize(legacy));
        await using var service = CreateService();

        var loaded = await service.GetInstalledAsync();

        loaded.Single().Custom!.Env.Should().BeNull();
        loaded.Single().Custom!.Args.Should().NotContain(secret);
        (await File.ReadAllTextAsync(_stateFile)).Should().NotContain(secret);
        (await File.ReadAllTextAsync(_stateFile)).Should().NotContain("API_TOKEN");
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
