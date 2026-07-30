using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Credentials;
using SranDesktop.Tests.Credentials;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// Where a credential must never end up.
///
/// Behavioural tests prove today's code keeps tokens out of the database, the
/// logs, and the worker's environment. These prove tomorrow's cannot quietly put
/// them back — a new call site that persists a token would pass every functional
/// test in the suite while making the secret recoverable from a backup.
/// </summary>
public sealed class CredentialLeakInvariantTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-leak-{Guid.NewGuid():N}");

    public CredentialLeakInvariantTests() => Directory.CreateDirectory(_root);

    // ── Storage ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task No_Token_Reaches_Any_File_Except_Protected_Storage()
    {
        const string accessToken = "at-live-9c1f7a2b4e6d8f0a";
        const string refreshToken = "rt-live-3b5d7f9a1c3e5g7i";
        var secrets = new InMemoryCredentialStore();
        var store = new DpapiConnectorCredentialStore(
            Path.Combine(_root, "connector-accounts.json"), secrets, NullLogger.Instance);

        await store.SaveAsync(
            new ConnectorCredentialDescriptor
            {
                Account = ConnectorAccountRef.For("mailer", "ana@example.test"),
                DisplayName = "ana@example.test",
                Scopes = ["mail.read"]
            },
            accessToken,
            refreshToken);

        foreach (var file in Directory.GetFiles(_root, "*", SearchOption.AllDirectories))
        {
            var text = await File.ReadAllTextAsync(file);
            text.Should().NotContain(accessToken, $"{Path.GetFileName(file)} is not protected");
            text.Should().NotContain(refreshToken, $"{Path.GetFileName(file)} is not protected");
        }

        // The tokens exist — they went to the protected bucket, not to a file.
        secrets.Values.Values.Should().ContainSingle(value =>
            value.Contains(accessToken, StringComparison.Ordinal));
    }

    [Fact]
    public void The_Credential_Descriptor_Type_Has_No_Place_To_Put_A_Token()
    {
        // The type that gets serialised into the listable index physically cannot
        // carry the secret. That is what makes "no raw token in ordinary storage"
        // a property of the shape rather than of everyone's discipline.
        var names = typeof(ConnectorCredentialDescriptor)
            .GetProperties()
            .Select(property => property.Name)
            .ToList();

        names.Should().NotContain(name =>
            name.Contains("AccessToken", StringComparison.OrdinalIgnoreCase)
            && name != "AccessTokenExpiresUtc");
        names.Should().NotContain(name =>
            name.Equals("RefreshToken", StringComparison.OrdinalIgnoreCase));
        names.Should().Contain("HasRefreshToken",
            "the existence of a refresh token is metadata; the token is not");
    }

    // ── Source-level invariants ─────────────────────────────────────────────

    [Fact]
    public void No_Production_File_Persists_A_Connector_Token_Through_The_Task_Store()
    {
        // The scheduler stores an account REFERENCE. A scheduled task that held a
        // token would put a credential into the same database as chat history,
        // where it would survive into every backup and diagnostic copy.
        var scheduled = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Application", "Models", "ScheduledTask.cs"));

        scheduled.Should().NotContain("AccessToken");
        scheduled.Should().NotContain("RefreshToken");
    }

    [Fact]
    public void Only_The_Credential_Layer_Reads_Connector_Secrets()
    {
        var offenders = ProductionSources()
            .Where(file => Path.GetFileName(file)
                is not "CredentialBroker.cs"
                and not "DpapiConnectorCredentialStore.cs"
                and not "ICredentialBroker.cs")
            .Where(file => File.ReadAllText(file)
                .Contains("ReadSecret(", StringComparison.Ordinal))
            .Select(Path.GetFileName)
            .ToArray();

        offenders.Should().BeEmpty(
            "a caller that reads the secret directly has bypassed every check the "
            + "lease exists to perform — revocation, expiry, and scope");
    }

    [Fact]
    public void The_Worker_Launcher_Never_Passes_A_Credential_Into_The_Environment()
    {
        // A worker is arbitrary code in a sandbox. A refresh token in its
        // environment would be readable by anything the worker runs and would
        // survive in the process table.
        var runner = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Mcp", "Tools",
            "WindowsAppContainerProcessRunner.cs"));

        foreach (var forbidden in new[]
                 {
                     "RefreshToken", "AccessToken", "ICredentialBroker",
                     "IConnectorCredentialStore"
                 })
            runner.Should().NotContain(forbidden,
                $"the isolated worker launcher must not know about {forbidden}");
    }

    [Fact]
    public void The_Token_Client_Does_Not_Propagate_Provider_Exception_Text()
    {
        // A token endpoint's failure carries the request, and the request carries
        // the credential. Rethrowing or logging ex.Message is the classic way a
        // token reaches a log file.
        var source = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Credentials",
            "OAuthTokenClient.cs"));

        source.Should().NotContain("ex.Message",
            "an exception from a token request can contain the token");
        source.Should().Contain("ex.GetType().Name",
            "the exception TYPE is safe to report and is enough to diagnose");
    }

    [Fact]
    public void A_Lease_Does_Not_Print_Its_Credential()
    {
        var source = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Credentials",
            "CredentialBroker.cs"));

        source.Should().Contain("public override string ToString()",
            "the default ToString would print the header value into any log line "
            + "that interpolates the lease");
        source.Should().Contain("redacted");
    }

    [Fact]
    public void The_Broker_Is_The_Only_Composed_Reader_Of_The_Credential_Store()
    {
        var host = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.App", "Hosting", "AppHostBuilder.cs"));

        host.Should().Contain("ICredentialBroker",
            "the broker has to be registered or nothing goes through it");
        host.Should().Contain("DpapiConnectorCredentialStore",
            "and it has to be backed by protected storage, not the app database");
        host.Should().NotContain("connector-accounts.json\", \"data.db",
            "the account index and the application database are different files");
    }

    private static string[] ProductionSources() =>
        new[] { "SranDesktop.Application", "SranDesktop.Infrastructure", "SranDesktop.Presentation" }
            .SelectMany(project => Directory.GetFiles(
                Path.Combine(RepositoryRoot(), "src", project), "*.cs",
                SearchOption.AllDirectories))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}",
                StringComparison.Ordinal))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}",
                StringComparison.Ordinal))
            .ToArray();

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException("The repository root was not found.");
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_root))
                Directory.Delete(_root, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }
}
