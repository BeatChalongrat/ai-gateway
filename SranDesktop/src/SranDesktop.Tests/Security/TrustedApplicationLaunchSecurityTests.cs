using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Security;

public sealed class TrustedApplicationLaunchSecurityTests
{
    [Theory]
    [InlineData(@"C:\Temp\payload.exe")]
    [InlineData("payload.exe --steal")]
    [InlineData("cmd.exe")]
    [InlineData("powershell")]
    [InlineData(@"\\server\share\payload.exe")]
    public void Arbitrary_Host_Executables_Are_Rejected(string requested)
    {
        OpenApplicationTool.TryResolveTrustedApplication(
                requested,
                out var executable,
                out var denial)
            .Should().BeFalse();
        executable.Should().BeEmpty();
        denial.Should().Contain("denied");
    }

    [Fact]
    public void System_Notepad_Is_Resolved_By_Deterministic_Identity()
    {
        var available = OpenApplicationTool.TryResolveTrustedApplication(
            "notepad",
            out var executable,
            out _);

        available.Should().BeTrue();
        executable.Should().Be(
            Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "notepad.exe"));
    }
}
