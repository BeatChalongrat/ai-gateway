using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// P3-4: a fresh install must not be able to reach zero files with no explanation.
///
/// <para>File tools reach exactly the folders the user granted, and there are no
/// implicit built-in roots — which is right, and is deliberately NOT being undone
/// here. The gap was onboarding: <c>GrantedFolders</c> and <c>WorkingFolder</c> both
/// start empty, so a new install configures zero mounts and every file tool, plus the
/// sandbox shell lane, fails closed while telling the user only that something was
/// denied.</para>
/// </summary>
[Collection("PathGuardGrants")]
public sealed class FirstRunWorkspaceTests : IDisposable
{
    [Fact]
    public void Onboarding_Proposes_A_Real_Grantable_Folder()
    {
        var suggestion = FolderGrantValidator.SuggestDefaultWorkingFolder();

        suggestion.Should().NotBeNull(
            "onboarding needs something concrete to offer; with nothing to propose the "
            + "user is back to a blank field and no idea it matters");
        FolderGrantValidator.Validate(suggestion, out _)
            .Should().Be(FolderGrantRejection.Ok,
                "a proposal the grant rules would reject is worse than no proposal");
        Directory.Exists(suggestion).Should().BeTrue();
    }

    [Fact]
    public void Suggestion_Is_Never_The_Apps_Own_State_Directory()
    {
        var suggestion = FolderGrantValidator.SuggestDefaultWorkingFolder();

        PathGuard.IsProtectedStatePath(suggestion!).Should().BeFalse();
    }

    /// <summary>The state a fresh install lands in if the user picks nothing: the
    /// error has to name the remedy, because no path the agent tries will work and a
    /// bare denial reads as "wrong folder" rather than "no folder yet".</summary>
    [Fact]
    public void With_No_Mounts_The_Error_Tells_The_Caller_What_To_Do()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>(), string.Empty);

        PathGuard.Resolve(Path.Combine(Path.GetTempPath(), "anything.txt"), out var error)
            .Should().BeNull();

        error.Should().NotBeNull();
        error.Should().Contain("working folder",
            "the message must name the thing that is missing");
        error.Should().Contain("Do not retry",
            "without this the agent burns its step budget trying other paths");
    }

    [Fact]
    public void With_No_Mounts_Relative_Paths_Fail_With_The_Same_Remedy()
    {
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>(), string.Empty);

        PathGuard.Resolve("notes.txt", out var error).Should().BeNull();

        error.Should().Contain("working folder");
    }

    /// <summary>Confirming the proposal is what makes file tools usable — the whole
    /// point of adding the step.</summary>
    [Fact]
    public void Confirming_The_Proposal_Makes_Files_Reachable()
    {
        var suggestion = FolderGrantValidator.SuggestDefaultWorkingFolder()!;

        PathGuard.ConfigureGrantedRoots(new[] { suggestion }, suggestion);

        PathGuard.Resolve(Path.Combine(suggestion, "note.txt"), out var error)
            .Should().NotBeNull($"the confirmed folder must be reachable. Error: {error}");
    }

    public void Dispose() =>
        PathGuard.ConfigureGrantedRoots(Array.Empty<string>(), string.Empty);
}
