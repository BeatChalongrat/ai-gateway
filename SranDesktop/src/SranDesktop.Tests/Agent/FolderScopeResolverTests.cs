using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Phase A PART 4: folder-scoped consent. Writes resolve to their containing
/// folder (one "Always allow" covers a project), shell resolves per-cwd (not a
/// blanket "*"). Joins the PathGuardGrants collection because it mutates the
/// static granted roots.
/// </summary>
[Collection("PathGuardGrants")]
public sealed class FolderScopeResolverTests : IDisposable
{
    private readonly string _tempSub;
    private readonly IMcpTool _write = new WriteFileTool();
    private readonly IMcpTool _shell = new RunCommandTool(NullLogger<RunCommandTool>.Instance);

    public FolderScopeResolverTests()
    {
        _tempSub = Path.Combine(Path.GetTempPath(), $"sran-scope-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_tempSub);
        PathGuard.ConfigureMounts(
            [new WorkspaceMount(_tempSub, WorkspaceMountAccess.ReadWrite)],
            _tempSub);
    }

    private static string Args(object o) => JsonSerializer.Serialize(o);

    [Fact]
    public void Writes_Under_Same_Root_Share_One_Scope()
    {
        var resolver = new FolderScopeResolver();
        var s1 = resolver.Resolve(_write, Args(new { path = Path.Combine(_tempSub, "a.txt") }), "c1");
        var s2 = resolver.Resolve(_write, Args(new { path = Path.Combine(_tempSub, "b.txt") }), "c1");
        var s3 = resolver.Resolve(_write, Args(new { path = Path.Combine(_tempSub, "deep", "c.txt") }), "c1");

        // One "Always allow" covers every file under the folder — not per file.
        s1.Should().Be(s2);
        s2.Should().Be(s3);
        s1.Should().NotBe(Path.Combine(_tempSub, "a.txt")); // a folder, not the exact path
    }

    [Fact]
    public void Granted_Folder_Is_The_Write_Scope_And_Distinct_From_Other_Roots()
    {
        var pub = Environment.GetEnvironmentVariable("PUBLIC");
        if (string.IsNullOrEmpty(pub) || !Directory.Exists(pub))
            return; // needs a writable dir outside temp/profile to be meaningful

        var granted = Path.Combine(pub, $"sran-scope-grant-{Guid.NewGuid():N}");
        Directory.CreateDirectory(granted);
        try
        {
            PathGuard.ConfigureGrantedRoots(new[] { granted });
            var resolver = new FolderScopeResolver();

            var insideGrant = resolver.Resolve(_write, Args(new { path = Path.Combine(granted, "x.txt") }), "c1");
            var insideTemp = resolver.Resolve(_write, Args(new { path = Path.Combine(_tempSub, "y.txt") }), "c1");

            insideGrant.Should().Be(Path.TrimEndingDirectorySeparator(granted));
            // A write in a different root is a different scope → still prompts.
            insideGrant.Should().NotBe(insideTemp);
        }
        finally
        {
            PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
            try { Directory.Delete(granted, recursive: true); } catch (IOException) { }
        }
    }

    [Fact]
    public void Shell_Scope_Is_Per_Cwd_Not_Wildcard()
    {
        var resolver = new FolderScopeResolver();

        var scoped = resolver.Resolve(_shell, Args(new { command = "echo hi", working_directory = _tempSub }), "c1");
        scoped.Should().NotBe("*");

        // No cwd → the user profile root, still not the old blanket "*".
        var defaultScope = resolver.Resolve(_shell, Args(new { command = "echo hi" }), "c1");
        defaultScope.Should().NotBe("*");
    }

    [Fact]
    public void Shell_Uses_Retained_Cwd_For_Scope()
    {
        var session = new ShellSession { CurrentConversationId = "c1" };
        session.SetWorkingDirectory("c1", _tempSub);
        var resolver = new FolderScopeResolver(session);

        var scoped = resolver.Resolve(_shell, Args(new { command = "echo hi" }), "c1");
        var explicitScope = resolver.Resolve(_shell, Args(new { command = "echo hi", working_directory = _tempSub }), "c1");

        scoped.Should().Be(explicitScope); // retained cwd scopes the same as explicit
    }

    [Fact]
    public void Out_Of_Root_Write_Gets_A_Unique_NonWildcard_Scope()
    {
        var resolver = new FolderScopeResolver();

        var scope = resolver.Resolve(_write, Args(new { path = "C:\\Windows\\System32\\evil.txt" }), "c1");

        // Never "*" (which could be blanket-approved) and never a broad allowed
        // root — the tool still hard-denies it at execution, but the scope must
        // not be trustable.
        scope.Should().NotBe("*");
        scope.Should().Contain("Windows");
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        try { if (Directory.Exists(_tempSub)) Directory.Delete(_tempSub, recursive: true); }
        catch (IOException) { }
    }
}
