using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Workers;

public interface IIsolatedWorkerHost
{
    Task<IsolatedWorkerOutcome> DispatchAsync(
        IsolatedWorkerRequest request,
        CancellationToken ct);
}

/// <summary>Maps a capability name to an installed worker image. Deliberately the
/// ONLY way a worker executable is chosen — no request field reaches this.</summary>
public interface IWorkerImageResolver
{
    IsolatedWorkerLaunch Resolve(string capability);
}

/// <summary>
/// Resolves worker images from the installed application layout
/// (<c>&lt;app&gt;/workers/&lt;capability&gt;/</c>). If the image is missing the
/// capability fails closed — there is no in-process fallback, which is the whole
/// point of moving this work out of the privileged process.
/// </summary>
public sealed class WorkerImageResolver : IWorkerImageResolver
{
    private static readonly IReadOnlyDictionary<string, string> EntryAssemblies =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["document"] = "SranDesktop.DocumentWorker.dll",
            ["mcp"] = "SranDesktop.McpWorker.dll",
            // Test-only image: proves the interactive transport against a worker
            // whose every failure mode is reachable on demand.
            ["interactive-fixture"] = "SranDesktop.InteractiveFixtureWorker.dll",
            ["mcp-fixture"] = "SranDesktop.McpFixtureServer.dll"
        };

    private readonly string _root;

    public WorkerImageResolver(string? workersRoot = null) =>
        _root = workersRoot ?? Path.Combine(AppContext.BaseDirectory, "workers");

    public IsolatedWorkerLaunch Resolve(string capability)
    {
        if (!EntryAssemblies.TryGetValue(capability, out var entryAssembly))
            throw new SandboxUnavailableException(
                $"CapabilityUnavailable: '{capability}' is not a known isolated worker.");

        var directory = Path.Combine(_root, capability.ToLowerInvariant());
        if (!Directory.Exists(directory)
            || !File.Exists(Path.Combine(directory, entryAssembly)))
            throw new SandboxUnavailableException(
                $"CapabilityUnavailable: the '{capability}' worker image is not installed at "
                + $"'{directory}'. The capability fails closed; there is no host fallback.");

        return new IsolatedWorkerLaunch(capability, directory, entryAssembly);
    }
}
