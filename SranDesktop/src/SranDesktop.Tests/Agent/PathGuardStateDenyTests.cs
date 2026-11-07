using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase 10 D1: the app's own state (conversation DB, DPAPI
/// credentials, settings, connectors, skills, WebView2 profile) is HARD-DENIED
/// for the file tools — it lives under the user-profile built-in root and
/// read_file is consent-free, so without the deny, model output could
/// exfiltrate the whole chat history with zero prompts. Joins the
/// "PathGuardGrants" collection: grants are static state (reset in Dispose).</summary>
[Collection("PathGuardGrants")]
public sealed class PathGuardStateDenyTests : IDisposable
{
    private static readonly string RoamingState = FolderGrantValidator.AppStateDir;
    private static readonly string LocalState = FolderGrantValidator.LocalAppStateDir;

    // Marker files created inside the REAL state dirs (dirs are kept; only
    // files we created are removed in Dispose).
    private readonly List<string> _createdFiles = new();

    public PathGuardStateDenyTests()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        Directory.CreateDirectory(RoamingState);
        Directory.CreateDirectory(LocalState);
    }

    private static string? Resolve(string path, out string? error) =>
        PathGuard.Resolve(path, out error);

    private static JsonElement Args(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private static string J(string path) => JsonSerializer.Serialize(path);

    public static TheoryData<string> StatePaths() => new()
    {
        Path.Combine(RoamingState, "data.db"),                       // SqliteConversationStore
        Path.Combine(RoamingState, "credentials.dat"),               // DpapiCredentialStore
        Path.Combine(RoamingState, "settings.json"),                 // JsonSettingsService
        Path.Combine(RoamingState, "connectors.json"),               // McpConnectorService
        Path.Combine(RoamingState, "projects.json"),                 // JsonProjectStore
        Path.Combine(RoamingState, "tasks.db"),                      // SqliteScheduledTaskStore
        Path.Combine(RoamingState, "skills", "user", "x", "SKILL.md"), // SkillManager
        Path.Combine(RoamingState, "logs", "app.log"),               // AppHostBuilder
        RoamingState,                                                // the dir itself
        Path.Combine(LocalState, "WebView2", "EBWebView", "Default", "Cookies"),
        Path.Combine(LocalState, "updates", "installer.exe"),        // UpdateService
        LocalState,
    };

    [Theory]
    [MemberData(nameof(StatePaths))]
    public void Resolve_Denies_Every_App_State_Path(string path)
    {
        Resolve(path, out var error).Should().BeNull();
        error.Should().Contain("application data");
    }

    [Fact]
    public void Resolve_Denies_DotDot_Normalization_Into_State_Dir()
    {
        // "...\SRAN Desktop\decoy\..\data.db" canonicalizes INTO the state dir.
        var sneaky = Path.Combine(RoamingState, "decoy", "..", "data.db");
        Resolve(sneaky, out var error).Should().BeNull();
        error.Should().Contain("application data");
    }

    [Fact]
    public void Resolve_Denies_Relative_Path_Into_State_Dir()
    {
        // FIX26 Part 3: bare relative paths resolve into the FIRST GRANTED
        // folder. Grant the profile for this test so ``AppData\\Roaming\\…``
        // lands on the state dir — which must still be hard-denied, grant or
        // not. Skip if this machine's layout differs.
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var relative = Path.GetRelativePath(profile, RoamingState);
        if (relative.StartsWith("..", StringComparison.Ordinal) || Path.IsPathFullyQualified(relative))
            return;

        PathGuard.ConfigureGrantedRoots(new[] { profile });
        try
        {
            Resolve(Path.Combine(relative, "settings.json"), out var error).Should().BeNull();
            error.Should().Contain("application data");
        }
        finally
        {
            PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        }
    }

    [Fact]
    public void Resolve_Still_Allows_Normal_Profile_Temp_And_Granted_Paths()
    {
        // FIX26 Part 3: a profile document is allowed only when its folder is
        // granted — the profile itself is no longer an implicit root.
        var documents = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Documents");
        var profileDoc = Path.Combine(documents, "doc.txt");
        Resolve(profileDoc, out _).Should().BeNull();
        if (Directory.Exists(documents))
        {
            PathGuard.ConfigureGrantedRoots(new[] { documents });
            try { Resolve(profileDoc, out _).Should().NotBeNull(); }
            finally { PathGuard.ConfigureGrantedRoots(Array.Empty<string>()); }
        }

        var tempFile = Path.Combine(Path.GetTempPath(), "sran-allowed.txt");
        Resolve(tempFile, out _).Should().NotBeNull();

        // Sibling of the state dir (e.g. %APPDATA%\OtherApp) stays reachable —
        // the deny is scoped to the SRAN Desktop subtrees, nothing wider.
        var sibling = Path.Combine(Path.GetDirectoryName(RoamingState)!, "OtherApp", "file.txt");
        PathGuard.ConfigureGrantedRoots(new[] { Path.GetDirectoryName(RoamingState)! }); // FIX26: needs a grant now
        try { Resolve(sibling, out _).Should().NotBeNull(); }
        finally { PathGuard.ConfigureGrantedRoots(Array.Empty<string>()); }

        // A granted folder still works, and a grant can never re-open the
        // state dir (deny is checked before ALL allow-roots).
        var grantDir = Path.Combine(Path.GetTempPath(), $"sran-deny-grant-{Guid.NewGuid():N}");
        Directory.CreateDirectory(grantDir);
        try
        {
            PathGuard.ConfigureGrantedRoots(new[] { grantDir });
            Resolve(Path.Combine(grantDir, "inside.txt"), out _).Should().NotBeNull();
            Resolve(Path.Combine(RoamingState, "data.db"), out _).Should().BeNull();
        }
        finally
        {
            PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
            Directory.Delete(grantDir, recursive: true);
        }
    }

    [Fact]
    public async Task ReadFileTool_Denies_State_Files()
    {
        foreach (var target in new[]
                 {
                     Path.Combine(RoamingState, "data.db"),
                     Path.Combine(RoamingState, "credentials.dat"),
                     Path.Combine(RoamingState, "settings.json"),
                 })
        {
            var result = await new ReadFileTool().ExecuteAsync(
                Args($"{{\"path\":{J(target)}}}"), CancellationToken.None);
            result.Success.Should().BeFalse(because: $"{target} is app state");
            result.Error.Should().Contain("application data");
        }
    }

    [Fact]
    public async Task WriteFileTool_Denies_And_Does_Not_Create_State_Files()
    {
        var probe = Path.Combine(RoamingState, $"phase10-write-probe-{Guid.NewGuid():N}.txt");
        _createdFiles.Add(probe); // if the deny ever regresses, Dispose removes the leak

        var result = await new WriteFileTool().ExecuteAsync(
            Args($"{{\"path\":{J(probe)},\"content\":\"pwned\"}}"), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("application data");
        File.Exists(probe).Should().BeFalse("a denied write must not touch disk");
    }

    [Fact]
    public async Task ListDirectoryTool_Denies_State_Dir_And_Hides_It_From_Parent_Listings()
    {
        // Direct targeting → denied outright.
        var direct = await new ListDirectoryTool().ExecuteAsync(
            Args($"{{\"path\":{J(RoamingState)}}}"), CancellationToken.None);
        direct.Success.Should().BeFalse();
        direct.Error.Should().Contain("application data");

        // Listing the allowed PARENT (%APPDATA%) must not surface the subtree.
        var parent = Path.GetDirectoryName(RoamingState)!;
        PathGuard.ConfigureGrantedRoots(new[] { parent }); // FIX26: the parent must be granted now (Dispose resets)
        var listing = await new ListDirectoryTool().ExecuteAsync(
            Args($"{{\"path\":{J(parent)}}}"), CancellationToken.None);
        listing.Success.Should().BeTrue();
        listing.Content.Should().NotContain("SRAN Desktop");
    }

    [Fact]
    public async Task SearchFilesTool_Denies_State_Dir_And_Skips_Its_Files_From_Parent_Searches()
    {
        // Direct targeting → denied outright.
        var direct = await new SearchFilesTool().ExecuteAsync(
            Args($"{{\"directory\":{J(RoamingState)},\"pattern\":\"*\"}}"), CancellationToken.None);
        direct.Success.Should().BeFalse();
        direct.Error.Should().Contain("application data");

        // A recursive search under the allowed parent must not find files
        // inside the state dir. The marker name is unique to this test run.
        var marker = $"phase10-deny-marker-{Guid.NewGuid():N}.txt";
        var markerPath = Path.Combine(RoamingState, marker);
        await File.WriteAllTextAsync(markerPath, "secret");
        _createdFiles.Add(markerPath);

        var parent = Path.GetDirectoryName(RoamingState)!;
        PathGuard.ConfigureGrantedRoots(new[] { parent }); // FIX26: the parent must be granted now (Dispose resets)
        var search = await new SearchFilesTool().ExecuteAsync(
            Args($"{{\"directory\":{J(parent)},\"pattern\":{J(marker)}}}"), CancellationToken.None);

        search.Success.Should().BeTrue();
        search.Content.Should().NotContain(marker);
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        foreach (var file in _createdFiles)
        {
            try
            {
                if (File.Exists(file)) File.Delete(file);
            }
            catch (IOException) { }
        }
    }
}

/// <summary>Phase 10 D1 (grant side): the LOCAL app-state dir joins the
/// roaming one as a forbidden grant.</summary>
public class FolderGrantValidatorLocalStateTests
{
    [Fact]
    public void Local_App_State_Dir_Is_Rejected()
    {
        Directory.CreateDirectory(FolderGrantValidator.LocalAppStateDir);
        FolderGrantValidator.Validate(FolderGrantValidator.LocalAppStateDir, out _)
            .Should().Be(FolderGrantRejection.SystemFolder);
    }
}
