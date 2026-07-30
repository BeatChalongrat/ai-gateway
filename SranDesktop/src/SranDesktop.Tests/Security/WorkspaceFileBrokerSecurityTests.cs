using System.Diagnostics;
using System.Text.RegularExpressions;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Security;

[AttributeUsage(AttributeTargets.Method)]
public sealed class PrivilegedWindowsFactAttribute : FactAttribute
{
    public PrivilegedWindowsFactAttribute()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"sran-symlink-discovery-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(root);
            var target = Path.Combine(root, "target.txt");
            var link = Path.Combine(root, "link.txt");
            File.WriteAllText(target, "probe");
            File.CreateSymbolicLink(link, target);
        }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException)
        {
            Skip = "Windows symbolic-link privilege or Developer Mode is unavailable.";
        }
        finally
        {
            try
            {
                if (Directory.Exists(root))
                    Directory.Delete(root, recursive: true);
            }
            catch (IOException)
            {
            }
        }
    }
}

public sealed class WorkspaceFileBrokerSecurityTests : IDisposable
{
    private readonly string _root;
    private readonly string _workspace;
    private readonly string _outside;
    private readonly WorkspaceFileBroker _broker;

    public WorkspaceFileBrokerSecurityTests()
    {
        _root = Path.Combine(Path.GetTempPath(), $"sran-fs-boundary-{Guid.NewGuid():N}");
        _workspace = Path.Combine(_root, "workspace");
        _outside = Path.Combine(_root, "outside");
        Directory.CreateDirectory(_workspace);
        Directory.CreateDirectory(_outside);
        _broker = new WorkspaceFileBroker(
            [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWrite)],
            _workspace);
    }

    [Fact]
    public async Task Normal_File_Inside_Workspace_RoundTrips()
    {
        var target = Path.Combine(_workspace, "nested", "file.txt");

        var write = await _broker.WriteTextAsync(target, "inside", CancellationToken.None);
        var read = await _broker.ReadFileAsync(target, 1024, CancellationToken.None);

        write.Length.Should().Be(6);
        System.Text.Encoding.UTF8.GetString(read.Bytes).Should().Be("inside");
    }

    [Theory]
    [InlineData(@"\\server\share\secret.txt")]
    [InlineData(@"\\?\C:\Windows\win.ini")]
    [InlineData(@"\\.\C:\Windows\win.ini")]
    [InlineData(@"C:\Windows\win.ini:secret")]
    public async Task Special_Path_Syntax_Is_Denied(string path)
    {
        Func<Task> action = () => _broker.ReadFileAsync(path, 1024, CancellationToken.None);
        await action.Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [Fact]
    public async Task Traversal_And_Absolute_Outside_Are_Denied()
    {
        var outsideFile = Path.Combine(_outside, "secret.txt");
        await File.WriteAllTextAsync(outsideFile, "secret");

        var traversal = Path.Combine(_workspace, "..", "outside", "secret.txt");
        await ((Func<Task>)(() => _broker.ReadFileAsync(traversal, 1024, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
        await ((Func<Task>)(() => _broker.WriteTextAsync(outsideFile, "changed", CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
        (await File.ReadAllTextAsync(outsideFile)).Should().Be("secret");
    }

    [Fact]
    public async Task Similar_Prefix_Directory_Is_Not_A_Mount_Descendant()
    {
        var sibling = _workspace + "-attacker";
        Directory.CreateDirectory(sibling);
        var target = Path.Combine(sibling, "secret.txt");
        await File.WriteAllTextAsync(target, "secret");

        await ((Func<Task>)(() => _broker.ReadFileAsync(target, 1024, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [PrivilegedWindowsFact]
    [Trait("Category", "PrivilegedWindowsSecurity")]
    public async Task File_Symlink_To_Outside_Is_Denied()
    {
        var outsideFile = Path.Combine(_outside, "secret.txt");
        var link = Path.Combine(_workspace, "link.txt");
        await File.WriteAllTextAsync(outsideFile, "secret");
        File.CreateSymbolicLink(link, outsideFile);

        await ((Func<Task>)(() => _broker.ReadFileAsync(link, 1024, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [PrivilegedWindowsFact]
    [Trait("Category", "PrivilegedWindowsSecurity")]
    public async Task Nested_Directory_Symlinks_To_Outside_Are_Denied()
    {
        var outsideFile = Path.Combine(_outside, "secret.txt");
        await File.WriteAllTextAsync(outsideFile, "secret");
        var first = Path.Combine(_workspace, "first");
        var second = Path.Combine(_workspace, "second");
        Directory.CreateSymbolicLink(first, _outside);
        Directory.CreateSymbolicLink(second, first);

        await ((Func<Task>)(() => _broker.ReadFileAsync(
                Path.Combine(second, "secret.txt"),
                1024,
                CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [PrivilegedWindowsFact]
    [Trait("Category", "PrivilegedWindowsSecurity")]
    public async Task Directory_Symlink_To_Outside_Is_Denied()
    {
        var outsideFile = Path.Combine(_outside, "directory-link-secret.txt");
        await File.WriteAllTextAsync(outsideFile, "secret");
        var link = Path.Combine(_workspace, "directory-link");
        Directory.CreateSymbolicLink(link, _outside);

        await ((Func<Task>)(() => _broker.ReadFileAsync(
                Path.Combine(link, "directory-link-secret.txt"),
                1024,
                CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [Fact]
    public async Task Junction_To_Outside_Is_Denied_And_Not_Traversed_By_Search()
    {
        var outsideFile = Path.Combine(_outside, "junction-secret.txt");
        await File.WriteAllTextAsync(outsideFile, "needle");
        var junction = Path.Combine(_workspace, "junction");
        CreateJunction(junction, _outside);

        await ((Func<Task>)(() => _broker.ReadFileAsync(
                Path.Combine(junction, "junction-secret.txt"),
                1024,
                CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();

        var matches = await _broker.SearchAsync(
            _workspace,
            "*.txt",
            new Regex("needle", RegexOptions.None, TimeSpan.FromSeconds(1)),
            10,
            1024,
            CancellationToken.None);
        matches.Should().BeEmpty();
    }

    [Fact]
    public async Task Nested_Junction_Chain_To_Outside_Is_Denied()
    {
        var outsideFile = Path.Combine(_outside, "nested-junction-secret.txt");
        await File.WriteAllTextAsync(outsideFile, "secret");
        var first = Path.Combine(_workspace, "junction-first");
        var second = Path.Combine(_workspace, "junction-second");
        CreateJunction(first, _outside);
        CreateJunction(second, first);

        await ((Func<Task>)(() => _broker.ReadFileAsync(
                Path.Combine(second, "nested-junction-secret.txt"),
                1024,
                CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [PrivilegedWindowsFact]
    [Trait("Category", "PrivilegedWindowsSecurity")]
    public async Task Mixed_Symlink_Junction_Chain_To_Outside_Is_Denied()
    {
        var outsideFile = Path.Combine(_outside, "mixed-secret.txt");
        await File.WriteAllTextAsync(outsideFile, "secret");
        var junction = Path.Combine(_workspace, "mixed-junction");
        var symlink = Path.Combine(_workspace, "mixed-symlink");
        CreateJunction(junction, _outside);
        Directory.CreateSymbolicLink(symlink, junction);

        await ((Func<Task>)(() => _broker.ReadFileAsync(
                Path.Combine(symlink, "mixed-secret.txt"),
                1024,
                CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
    }

    [Fact]
    public async Task ReadOnly_And_NoDelete_Mount_Permissions_Are_Enforced()
    {
        var existing = Path.Combine(_workspace, "existing.txt");
        await File.WriteAllTextAsync(existing, "original");

        var readOnly = new WorkspaceFileBroker(
            [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadOnly)],
            _workspace);
        await ((Func<Task>)(() =>
                readOnly.WriteTextAsync(existing, "changed", CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();

        var noDelete = new WorkspaceFileBroker(
            [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWriteNoDelete)],
            _workspace);
        await ((Func<Task>)(() => noDelete.MoveAsync(
                existing,
                Path.Combine(_workspace, "renamed.txt"),
                false,
                CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
        (await File.ReadAllTextAsync(existing)).Should().Be("original");
    }

    [Fact]
    public async Task Move_Across_Mount_Boundary_Is_Denied_Without_Effect()
    {
        var source = Path.Combine(_workspace, "source.txt");
        var destination = Path.Combine(_outside, "destination.txt");
        await File.WriteAllTextAsync(source, "source");

        await ((Func<Task>)(() =>
                _broker.MoveAsync(source, destination, false, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
        File.Exists(source).Should().BeTrue();
        File.Exists(destination).Should().BeFalse();
    }

    [Fact]
    public async Task Replacing_Workspace_After_Configuration_Is_Detected()
    {
        var original = _workspace + "-original";
        Directory.Move(_workspace, original);
        Directory.CreateDirectory(_workspace);
        var replacementFile = Path.Combine(_workspace, "replacement.txt");
        await File.WriteAllTextAsync(replacementFile, "replacement");

        await ((Func<Task>)(() =>
                _broker.ReadFileAsync(replacementFile, 1024, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>()
            .WithMessage("*changed after authorization*");
    }

    [Fact]
    public async Task Junction_Retargeted_After_A_Legitimate_Read_Is_Denied()
    {
        var inside = Path.Combine(_workspace, "inside-target");
        Directory.CreateDirectory(inside);
        await File.WriteAllTextAsync(Path.Combine(inside, "data.txt"), "legitimate");
        await File.WriteAllTextAsync(
            Path.Combine(_outside, "data.txt"),
            "secret-after-retarget");
        var junction = Path.Combine(_workspace, "swappable");
        CreateJunction(junction, inside);
        var path = Path.Combine(junction, "data.txt");

        var before = await _broker.ReadFileAsync(path, 1024, CancellationToken.None);
        System.Text.Encoding.UTF8.GetString(before.Bytes).Should().Be("legitimate");

        // Same path, repointed between calls — resolution must happen per access,
        // never be cached from the first authorization.
        Directory.Delete(junction);
        CreateJunction(junction, _outside);

        await ((Func<Task>)(() => _broker.ReadFileAsync(path, 1024, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();

        // Drop the reparse point here: a recursive delete of the workspace would
        // otherwise follow it into the outside directory.
        Directory.Delete(junction);
    }

    [Fact]
    public async Task Short_Name_Alias_Of_An_Outside_Path_Is_Denied()
    {
        var longDirectory = Path.Combine(_outside, "a-very-long-directory-name");
        Directory.CreateDirectory(longDirectory);
        var secret = Path.Combine(longDirectory, "short-name-secret.txt");
        await File.WriteAllTextAsync(secret, "secret");
        var alias = ShortPath(secret);

        // 8.3 generation is off on some volumes; then `alias` is just the long
        // path and this still asserts the outside-path denial.
        await ((Func<Task>)(() => _broker.ReadFileAsync(alias, 1024, CancellationToken.None)))
            .Should().ThrowAsync<WorkspaceAccessDeniedException>();
        (await File.ReadAllTextAsync(secret)).Should().Be("secret");
    }

    private static string ShortPath(string path)
    {
        var buffer = new System.Text.StringBuilder(1024);
        var length = GetShortPathNameW(path, buffer, buffer.Capacity);
        return length is > 0 && length < buffer.Capacity ? buffer.ToString() : path;
    }

    [System.Runtime.InteropServices.DllImport(
        "kernel32.dll",
        CharSet = System.Runtime.InteropServices.CharSet.Unicode,
        SetLastError = true)]
    private static extern int GetShortPathNameW(
        string longPath,
        System.Text.StringBuilder shortPath,
        int bufferSize);

    [Fact]
    public async Task Case_Variation_Inside_Same_Windows_Mount_Is_Allowed()
    {
        var file = Path.Combine(_workspace, "CaseFile.txt");
        await File.WriteAllTextAsync(file, "case");
        var varied = file.ToUpperInvariant();

        var snapshot = await _broker.ReadFileAsync(varied, 1024, CancellationToken.None);
        System.Text.Encoding.UTF8.GetString(snapshot.Bytes).Should().Be("case");
    }

    private static void CreateJunction(string junction, string target)
    {
        using var process = Process.Start(new ProcessStartInfo
        {
            FileName = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "cmd.exe"),
            ArgumentList = { "/d", "/s", "/c", "mklink", "/J", junction, target },
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        }) ?? throw new InvalidOperationException("Unable to start mklink.");
        process.WaitForExit();
        if (process.ExitCode != 0)
            throw new InvalidOperationException(
                $"Unable to create test junction: {process.StandardError.ReadToEnd()}");
    }

    public void Dispose()
    {
        foreach (var entry in new[]
                 {
                     Path.Combine(_workspace, "junction"),
                     Path.Combine(_workspace, "junction-second"),
                     Path.Combine(_workspace, "junction-first"),
                     Path.Combine(_workspace, "mixed-symlink"),
                     Path.Combine(_workspace, "mixed-junction"),
                     Path.Combine(_workspace, "directory-link"),
                     Path.Combine(_workspace, "second"),
                     Path.Combine(_workspace, "first")
                 })
        {
            try
            {
                if (Directory.Exists(entry))
                    Directory.Delete(entry);
            }
            catch (IOException)
            {
            }
        }

        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
    }
}
