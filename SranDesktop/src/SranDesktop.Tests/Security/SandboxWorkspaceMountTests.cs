using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// The connector sandbox has to be able to mount its own workspace, including
/// when that workspace is inside the app's state tree.
///
/// This is the defect the packaged-approval run surfaced. The default connector
/// root is %LOCALAPPDATA%\SRAN Desktop\connectors, so the per-connector scratch
/// directory the launcher creates lives under a path the user-grant filter denies
/// wholesale. Configure dropped the only mount, ContainingRoot answered null, and
/// every local stdio connector failed to launch with "the working directory is
/// not inside an authorized workspace mount" — on a real installation, while
/// every end-to-end test stayed green, because they all redirected the connector
/// root to a temp directory.
///
/// The exemption is narrow on purpose, and the second test is the one that keeps
/// it narrow: the agent-facing broker must still refuse the same subtree.
/// </summary>
[Collection("PathGuardGrants")]
public sealed class SandboxWorkspaceMountTests : IDisposable
{
    private readonly string _stateWorkspace = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SRAN Desktop", "connectors", "workspaces", $"mount-test-{Guid.NewGuid():N}");

    public SandboxWorkspaceMountTests() => Directory.CreateDirectory(_stateWorkspace);

    [Fact]
    public void A_Sandbox_Workspace_Inside_The_App_State_Tree_Is_Mountable()
    {
        PathGuard.IsProtectedStatePath(_stateWorkspace).Should().BeTrue(
            "this test is only meaningful while that subtree is state-protected");

        var broker = WorkspaceFileBroker.ForSandboxWorkspace(_stateWorkspace);

        broker.ContainingRoot(_stateWorkspace).Should().NotBeNull(
            "the launcher created this directory; refusing it blocks every local "
            + "connector on a default installation");
        broker.ContainingRoot(Path.Combine(_stateWorkspace, "server", "out.txt"))
            .Should().NotBeNull("paths under the workspace are inside the mount");
    }

    [Fact]
    public void The_Agent_Facing_Broker_Still_Refuses_That_Subtree()
    {
        // Same directory, ordinary construction — the grant path the settings UI
        // uses. The exemption must not have leaked into it.
        var granted = new WorkspaceFileBroker(
            [new WorkspaceMount(_stateWorkspace, WorkspaceMountAccess.ReadWrite)]);

        granted.ContainingRoot(_stateWorkspace).Should().BeNull(
            "a user grant over the app's own state is still refused");
    }

    [Fact]
    public void A_Sandbox_Workspace_Confines_The_Server_To_Itself()
    {
        var broker = WorkspaceFileBroker.ForSandboxWorkspace(_stateWorkspace);

        // The waiver is about WHICH root may be mounted, never about how far the
        // mount reaches: everything outside it is still off limits.
        broker.ContainingRoot(Path.GetDirectoryName(_stateWorkspace)!)
            .Should().BeNull("the parent holds every other connector's workspace");
        broker.ContainingRoot(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "SRAN Desktop", "connector-trust.json"))
            .Should().BeNull("the trust file authorising the connector stays unreachable");
    }

    [Fact]
    public void Only_The_Connector_Launcher_May_Waive_The_State_Guard()
    {
        // The waiver is safe because exactly one caller uses it, on a directory
        // it created itself. A second call site is not automatically wrong, but
        // it is a decision that has to be made deliberately rather than by
        // copying a line — so it fails here and gets read.
        var infrastructure = Path.Combine(RepositoryRoot(), "src", "SranDesktop.Infrastructure");
        var callers = Directory
            .EnumerateFiles(infrastructure, "*.cs", SearchOption.AllDirectories)
            .Where(file => File.ReadAllText(file).Contains(
                "WorkspaceFileBroker.ForSandboxWorkspace", StringComparison.Ordinal))
            .Select(Path.GetFileName)
            .ToList();

        callers.Should().BeEquivalentTo(["IsolatedMcpConnectorSessionProvider.cs"]);
    }

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException("The repository root was not found.");
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_stateWorkspace))
                Directory.Delete(_stateWorkspace, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }
}
