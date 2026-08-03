using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>FIX-F: read-class tools are consent-free INSIDE the user's chosen
/// folders but must ask when they leave them (e.g. ~/.ssh). Joins the
/// "PathGuardGrants" collection: grants are static state (reset in Dispose).</summary>
[Collection("PathGuardGrants")]
public sealed class SensitiveReadGuardTests : IDisposable
{
    private readonly string _grantedDir;
    private readonly SensitiveReadGuard _guard = new();

    public SensitiveReadGuardTests()
    {
        _grantedDir = Path.Combine(Path.GetTempPath(), $"sran-scope-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_grantedDir);
        PathGuard.ConfigureGrantedRoots(new[] { _grantedDir });
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        try { Directory.Delete(_grantedDir, recursive: true); } catch (IOException) { }
    }

    private sealed class StubTool : IMcpTool
    {
        public StubTool(string name) => Name = name;
        public string Name { get; }
        public string Description => "stub";
        public JsonElement ParametersSchema => JsonDocument.Parse("{}").RootElement.Clone();
        public ToolCategory Category => ToolCategory.FileSystem;
        public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;
        public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
            Task.FromResult(ToolResult.Ok("ok"));
    }

    private static string Args(string name, string value) =>
        JsonSerializer.Serialize(new Dictionary<string, string> { [name] = value });

    [Fact]
    public void Read_Inside_A_Granted_Folder_Stays_Consent_Free()
    {
        var inside = Path.Combine(_grantedDir, "notes.txt");
        _guard.RequiresConsent(new StubTool("read_file"), Args("path", inside))
            .Should().BeFalse();
    }

    [Fact]
    public void Read_Outside_The_Chosen_Scope_Requires_Consent()
    {
        var ssh = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".ssh", "id_rsa");
        _guard.RequiresConsent(new StubTool("read_file"), Args("path", ssh))
            .Should().BeTrue();
    }

    [Fact]
    public void List_And_Search_Follow_The_Same_Rule()
    {
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

        _guard.RequiresConsent(new StubTool("list_directory"), Args("path", profile))
            .Should().BeTrue();
        _guard.RequiresConsent(new StubTool("search_files"), Args("directory", profile))
            .Should().BeTrue();
        _guard.RequiresConsent(new StubTool("list_directory"), Args("path", _grantedDir))
            .Should().BeFalse();
    }

    [Fact]
    public void Non_Read_Tools_Are_Not_This_Guards_Business()
    {
        var outside = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "x.txt");
        _guard.RequiresConsent(new StubTool("write_file"), Args("path", outside))
            .Should().BeFalse("write_file already Asks via its own default");
    }

    [Fact]
    public void Paths_PathGuard_Already_Denies_Are_Left_To_PathGuard()
    {
        _guard.RequiresConsent(new StubTool("read_file"), Args("path", "Q:\\nowhere\\x.txt"))
            .Should().BeFalse("the tool itself will refuse with a clear error");
    }
}
