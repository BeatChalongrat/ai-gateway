using System.Text.Json;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// P3-2: the working folder is granted rename/delete; other granted folders are not.
///
/// <para>Why this mattered. Every mount was configured
/// <see cref="WorkspaceMountAccess.ReadWriteNoDelete"/>, and "no delete" also blocks
/// rename, because a rename consumes the source name. Two consequences followed, both
/// invisible in the test suite at the time:</para>
/// <list type="number">
/// <item><c>move_file</c> was registered but could never succeed anywhere.</item>
/// <item>The sandbox lane writes results back by deleting files the command removed,
/// so any sandboxed command that touched a temporary file finished correctly and then
/// reported <c>SandboxWorkspaceSyncFailed</c>.</item>
/// </list>
/// <para>"No delete" over a folder the agent can already overwrite byte-for-byte
/// prevented neither data loss nor escape — it only broke those two features. The
/// working folder is also the one directory the user pointed the agent at
/// deliberately, through the composer, for this session. Standing grants keep the
/// stricter rule.</para>
/// </summary>
[Collection("PathGuardGrants")]
public sealed class WorkingFolderMountAccessTests : IDisposable
{
    private readonly string _root;
    private readonly string _workingFolder;
    private readonly string _standingGrant;

    public WorkingFolderMountAccessTests()
    {
        _root = Path.Combine(Path.GetTempPath(), $"sran-mount-access-{Guid.NewGuid():N}");
        _workingFolder = Path.Combine(_root, "working");
        _standingGrant = Path.Combine(_root, "standing");
        Directory.CreateDirectory(_workingFolder);
        Directory.CreateDirectory(_standingGrant);
        PathGuard.ConfigureGrantedRoots(
            new[] { _standingGrant, _workingFolder },
            _workingFolder);
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    [Fact]
    public async Task Move_File_Succeeds_Inside_The_Working_Folder()
    {
        var source = Path.Combine(_workingFolder, "before.txt");
        var destination = Path.Combine(_workingFolder, "after.txt");
        await File.WriteAllTextAsync(source, "payload");

        var result = await new MoveFileTool().ExecuteAsync(
            Args(new { source, destination }), CancellationToken.None);

        result.Success.Should().BeTrue(
            "the user chose this folder for the agent to work in; renaming inside it "
            + $"must work. Error was: {result.Error}");
        File.Exists(destination).Should().BeTrue();
        File.Exists(source).Should().BeFalse();
        (await File.ReadAllTextAsync(destination)).Should().Be("payload");
    }

    [Fact]
    public async Task Move_File_Is_Still_Refused_Inside_A_Standing_Grant()
    {
        var source = Path.Combine(_standingGrant, "before.txt");
        await File.WriteAllTextAsync(source, "payload");

        var result = await new MoveFileTool().ExecuteAsync(
            Args(new { source, destination = Path.Combine(_standingGrant, "after.txt") }),
            CancellationToken.None);

        result.Success.Should().BeFalse(
            "a folder granted long ago for one task should not silently allow renames");
        File.Exists(source).Should().BeTrue("the source must be untouched after a refusal");
    }

    [Fact]
    public void Working_Folder_Mount_Is_ReadWrite_And_Standing_Grants_Are_Not()
    {
        // Asserted through the broker's own delete authorization rather than by
        // reading the mount record, so the test fails if the ENFORCEMENT changes and
        // not merely if the label does.
        var deletable = Path.Combine(_workingFolder, "scratch.txt");
        File.WriteAllText(deletable, "x");
        var protectedFile = Path.Combine(_standingGrant, "scratch.txt");
        File.WriteAllText(protectedFile, "x");

        var broker = WorkspaceFileBroker.Shared;

        broker.Invoking(b => b.DeleteAsync(deletable, CancellationToken.None).GetAwaiter().GetResult())
            .Should().NotThrow("the working folder permits delete");
        broker.Invoking(b => b.DeleteAsync(protectedFile, CancellationToken.None).GetAwaiter().GetResult())
            .Should().Throw<IOException>("a standing grant does not permit delete");
    }

    /// <summary>The sandbox's write-back deletes files the command removed. With the
    /// working folder read-only-ish that step threw, turning a correct run into a
    /// reported failure — so a delete inside the working folder has to be permitted
    /// for the sandbox lane to be able to report success at all.</summary>
    [Fact]
    public async Task Working_Folder_Permits_The_Delete_That_Sandbox_Write_Back_Performs()
    {
        var temporary = Path.Combine(_workingFolder, "build.tmp");
        await File.WriteAllTextAsync(temporary, "intermediate");

        await WorkspaceFileBroker.Shared.Invoking(
                b => b.DeleteAsync(temporary, CancellationToken.None))
            .Should().NotThrowAsync(
                "this is exactly the call SyncWorkspaceAsync makes for a file the "
                + "sandboxed command deleted; when it throws, the run is reported as "
                + "SandboxWorkspaceSyncFailed despite having succeeded");
        File.Exists(temporary).Should().BeFalse();
    }

    [Fact]
    public void Working_Folder_Is_Matched_Regardless_Of_Casing_Or_Trailing_Separator()
    {
        PathGuard.ConfigureGrantedRoots(
            new[] { _workingFolder },
            _workingFolder.ToUpperInvariant() + Path.DirectorySeparatorChar);

        var file = Path.Combine(_workingFolder, "case.txt");
        File.WriteAllText(file, "x");

        WorkspaceFileBroker.Shared
            .Invoking(b => b.DeleteAsync(file, CancellationToken.None).GetAwaiter().GetResult())
            .Should().NotThrow(
                "a differently spelled but identical directory is the same folder; "
                + "matching it by exact string would silently drop the grant");
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>(), string.Empty);
        try { Directory.Delete(_root, recursive: true); } catch (IOException) { }
    }
}
