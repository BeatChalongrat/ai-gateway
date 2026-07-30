using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;
using Xunit.Abstractions;

namespace SranDesktop.Tests.Security;

/// <summary>Environment probe: can a .NET worker actually start inside the
/// capability-free AppContainer? Answers whether the isolated document/MCP
/// workers can be .NET executables at all.</summary>
[Collection("PathGuardGrants")]
public sealed class AppContainerDotnetProbeTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-dotnet-probe-{Guid.NewGuid():N}");
    private readonly ITestOutputHelper _output;

    public AppContainerDotnetProbeTests(ITestOutputHelper output)
    {
        _output = output;
        Directory.CreateDirectory(_root);
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Probe_Dotnet_Host_Reachability_From_AppContainer()
    {
        var broker = new WorkspaceFileBroker(
            [new WorkspaceMount(_root, WorkspaceMountAccess.ReadWrite)],
            _root);
        var runner = new WindowsAppContainerProcessRunner(broker);
        var lines = new List<string>();
        var dotnet = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "dotnet",
            "dotnet.exe");
        var exit = -1;
        Exception? failure = null;
        try
        {
            exit = await runner.RunAsync(
                new ProcessRunSpec(
                    Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.System),
                        "cmd.exe"),
                    $"/d /q /s /c \"\"{dotnet}\" --version\"",
                    _root),
                lines.Add,
                CancellationToken.None);
        }
        catch (Exception ex)
        {
            failure = ex;
        }

        _output.WriteLine($"exit={exit} failure={failure?.GetType().Name}: {failure?.Message}");
        _output.WriteLine($"output=[{string.Join(" | ", lines)}]");
        // Probe only — records the environment answer, never gates the build.
        true.Should().BeTrue();
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
    }
}
