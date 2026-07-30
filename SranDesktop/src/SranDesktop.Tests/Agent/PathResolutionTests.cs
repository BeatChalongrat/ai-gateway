using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>A3: file destinations must land where the user meant. "Desktop"/"Documents"
/// tokens map to the real shell folder, and bare relative paths resolve against the
/// composer's working folder (when set) instead of silently defaulting to the home
/// directory — the real-run bug where a save dropped into C:\Users\&lt;user&gt;. Joins the
/// "PathGuardGrants" collection because it mutates PathGuard static state.</summary>
[Collection("PathGuardGrants")]
public sealed class PathResolutionTests : IDisposable
{
    public PathResolutionTests()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        PathGuard.ConfigureWorkingDirectory(null);
    }

    [Fact]
    public void Desktop_Token_Expands_To_The_Real_Desktop_Folder()
    {
        var desktop = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);

        PathGuard.ExpandUserFolders("Desktop").Should().Be(desktop);
        PathGuard.ExpandUserFolders("desktop\\report.txt")
            .Should().Be(Path.Combine(desktop, "report.txt"));
        PathGuard.ExpandUserFolders("Desktop/report.txt")
            .Should().Be(Path.Combine(desktop, "report.txt"));
    }

    [Fact]
    public void Documents_Token_Expands_To_The_Real_Documents_Folder()
    {
        var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);

        PathGuard.ExpandUserFolders("Documents\\a.txt")
            .Should().Be(Path.Combine(documents, "a.txt"));
    }

    [Fact]
    public void Fully_Qualified_And_Unknown_Tokens_Pass_Through_Unchanged()
    {
        PathGuard.ExpandUserFolders(@"C:\some\abs\path.txt").Should().Be(@"C:\some\abs\path.txt");
        PathGuard.ExpandUserFolders("notes.txt").Should().Be("notes.txt");
        PathGuard.ExpandUserFolders(@"project\src\a.cs").Should().Be(@"project\src\a.cs");
    }

    [Fact]
    public void Working_Folder_Token_And_Relative_Paths_Resolve_Against_The_Working_Directory()
    {
        var working = Path.Combine(Path.GetTempPath(), $"sran-wf-{Guid.NewGuid():N}");
        Directory.CreateDirectory(working);
        try
        {
            PathGuard.ConfigureMounts(
                [new WorkspaceMount(working, WorkspaceMountAccess.ReadWriteNoDelete)],
                working);
            var canonical = Path.TrimEndingDirectorySeparator(Path.GetFullPath(working));

            PathGuard.Resolve("working folder", out _).Should().Be(canonical);
            PathGuard.Resolve("note.txt", out _).Should().Be(Path.Combine(canonical, "note.txt"));
        }
        finally
        {
            PathGuard.ConfigureWorkingDirectory(null);
            Directory.Delete(working, recursive: true);
        }
    }

    [Fact]
    public void Relative_Paths_Fail_Closed_When_No_Working_Folder_Is_Set()
    {
        PathGuard.ConfigureWorkingDirectory(null);
        PathGuard.Resolve("plain.txt", out var error).Should().BeNull();
        error.Should().Contain("Relative paths require");
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        PathGuard.ConfigureWorkingDirectory(null);
    }
}
