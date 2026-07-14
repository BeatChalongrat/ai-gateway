using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase 6 Task 1: folder grants extend PathGuard's reach safely.
/// Serial to isolate the static _grantedRoots (reset in Dispose).</summary>
[Collection("PathGuardGrants")]
public sealed class PathGuardGrantTests : IDisposable
{
    // The grant dir must live OUTSIDE the built-in roots to prove grant/revoke
    // — but temp is INSIDE the user profile (both built-in), so a temp subdir
    // stays allowed regardless of grants. The Public folder (C:\Users\Public)
    // is writable and outside both; if it isn't available the affected asserts
    // are skipped via _outsideBuiltins.
    private readonly string _grantDir;
    private readonly string? _grantParent;
    private readonly bool _outsideBuiltins;

    public PathGuardGrantTests()
    {
        var pub = Environment.GetEnvironmentVariable("PUBLIC");
        _outsideBuiltins = !string.IsNullOrEmpty(pub) && Directory.Exists(pub);

        var baseDir = _outsideBuiltins ? pub! : Path.GetTempPath();
        _grantParent = Path.Combine(baseDir, $"sran-grant-{Guid.NewGuid():N}");
        _grantDir = Path.Combine(_grantParent, "workspace");
        Directory.CreateDirectory(_grantDir);
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
    }

    private static string? Resolve(string path) => PathGuard.Resolve(path, out _);

    [Fact]
    public void Granted_Root_Allows_Files_Inside()
    {
        // A path with a granted root works after configure; the resolver is
        // case-insensitive on Windows.
        PathGuard.ConfigureGrantedRoots(new[] { _grantDir });

        var inside = Path.Combine(_grantDir, "sub", "file.txt");
        Resolve(inside).Should().NotBeNull();
    }

    [Fact]
    public void Escape_From_Granted_Root_Is_Denied()
    {
        PathGuard.ConfigureGrantedRoots(new[] { _grantDir });

        // Many ..\ saturate at the drive root (Path.GetFullPath collapses the
        // excess), landing at <drive>:\Windows — outside the grant AND outside
        // the built-in roots (the user profile is <drive>:\Users\<user>). This
        // is exactly what a real "grant D:\work then read D:\work\..\secret"
        // canonicalizes to, so the denial proves ..\ can't escape a grant.
        var ups = Enumerable.Repeat("..", 12).ToArray();
        var escape = Path.Combine(new[] { _grantDir }.Concat(ups).Append("Windows").ToArray());
        Resolve(escape).Should().BeNull();
    }

    [Fact]
    public void Revoking_A_Grant_Takes_Effect_Immediately()
    {
        if (!_outsideBuiltins) return; // needs a dir outside temp/profile to be meaningful
        var inside = Path.Combine(_grantDir, "file.txt");
        PathGuard.ConfigureGrantedRoots(new[] { _grantDir });
        Resolve(inside).Should().NotBeNull();

        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        Resolve(inside).Should().BeNull(); // outside built-in roots again
    }

    [Fact]
    public void Built_In_Roots_Work_With_No_Grants()
    {
        var tempFile = Path.Combine(Path.GetTempPath(), "sran-builtin.txt");
        Resolve(tempFile).Should().NotBeNull();

        var profileFile = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "doc.txt");
        Resolve(profileFile).Should().NotBeNull();
    }

    [Fact]
    public void Forbidden_Grants_Are_Filtered_By_Configure()
    {
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var driveRoot = Path.GetPathRoot(windows)!;

        var applied = PathGuard.ConfigureGrantedRoots(new[] { windows, driveRoot, _grantDir });

        applied.Should().ContainSingle()
            .Which.Should().Be(Path.TrimEndingDirectorySeparator(_grantDir));
    }

    public void Dispose()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>());
        try
        {
            if (_grantParent is not null && Directory.Exists(_grantParent))
                Directory.Delete(_grantParent, recursive: true);
        }
        catch (IOException) { }
    }
}

/// <summary>Pure validator rules (no static state — safe to run in parallel).</summary>
public class FolderGrantValidatorTests
{
    [Fact]
    public void Existing_Directory_Is_Grantable()
    {
        var dir = Path.Combine(Path.GetTempPath(), $"sran-fgv-{Guid.NewGuid():N}");
        Directory.CreateDirectory(dir);
        try
        {
            FolderGrantValidator.Validate(dir, out var canonical)
                .Should().Be(FolderGrantRejection.Ok);
            canonical.Should().Be(Path.TrimEndingDirectorySeparator(dir));
        }
        finally { Directory.Delete(dir); }
    }

    [Fact]
    public void Drive_Root_Is_Rejected()
    {
        var driveRoot = Path.GetPathRoot(Environment.GetFolderPath(
            Environment.SpecialFolder.Windows))!;
        FolderGrantValidator.Validate(driveRoot, out _)
            .Should().Be(FolderGrantRejection.DriveRoot);
    }

    [Fact]
    public void Windows_And_ProgramFiles_Are_Rejected()
    {
        FolderGrantValidator.Validate(
            Environment.GetFolderPath(Environment.SpecialFolder.Windows), out _)
            .Should().Be(FolderGrantRejection.SystemFolder);
        FolderGrantValidator.Validate(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), out _)
            .Should().Be(FolderGrantRejection.SystemFolder);
    }

    [Fact]
    public void App_State_Dir_Is_Rejected()
    {
        var appState = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "SRAN Desktop");
        Directory.CreateDirectory(appState); // must exist to reach the forbidden check
        FolderGrantValidator.Validate(appState, out _)
            .Should().Be(FolderGrantRejection.SystemFolder);
    }

    [Fact]
    public void Nonexistent_Directory_Is_Rejected()
    {
        FolderGrantValidator.Validate(
            Path.Combine(Path.GetTempPath(), $"missing-{Guid.NewGuid():N}"), out _)
            .Should().Be(FolderGrantRejection.NotADirectory);
    }

    [Fact]
    public void Relative_Path_Is_Rejected()
    {
        FolderGrantValidator.Validate(@"relative\path", out _)
            .Should().Be(FolderGrantRejection.NotAbsolute);
        FolderGrantValidator.Validate("", out _)
            .Should().Be(FolderGrantRejection.NotAbsolute);
    }
}
