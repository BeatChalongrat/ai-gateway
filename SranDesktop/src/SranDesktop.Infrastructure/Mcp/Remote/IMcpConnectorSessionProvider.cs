using SranDesktop.Application.Models;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// One live, initialized connection to a connector's server.
///
/// <see cref="Generation"/> and <see cref="ServerIdentity"/> together are what
/// make a post-disconnect retry checkable: repeating a read is only safe if the
/// thing answering is demonstrably the same server we asked, not whatever
/// process now owns that stdio pipe.
/// </summary>
public sealed record McpConnectorSession(
    string ConnectorId,
    McpRemoteClient Client,
    long Generation,
    string ServerIdentity,
    McpServerCapabilities Capabilities);

/// <summary>
/// Supplies initialized sessions, the approved manifest, and the approved risk
/// policy for a connector. Split out from the gateway so the gateway can be
/// tested against a real sandboxed server without dragging in the whole
/// marketplace/install stack.
/// </summary>
public interface IMcpConnectorSessionProvider
{
    /// <summary>The manifest as approved. Null when the connector is not
    /// installed — which is a refusal, not a reason to dial anything.</summary>
    ConnectorManifest? GetManifest(string connectorId);

    /// <summary>The risk policy the user approved. Undeclared for a connector
    /// installed without one, which forces conservative classification.</summary>
    ConnectorRiskPolicy GetRiskPolicy(string connectorId);

    /// <summary>
    /// The full verdict: either a launch descriptor or a NAMED refusal.
    ///
    /// The name matters more than it looks. "Not installed", "never approved",
    /// "the binary changed", and "it now wants the network" are four different
    /// problems with four different remedies, and collapsing them into a null
    /// manifest tells the user — and the log — nothing about which one they have.
    ///
    /// The default here just surfaces the approved manifest a provider already
    /// holds and leaves the verdict to the gateway's own trust gate, which knows
    /// the installation policy; the production implementation resolves the whole
    /// thing from disk and can therefore name the refusal precisely.
    /// </summary>
    ConnectorResolution Resolve(string connectorId) =>
        McpConnectorResolution.FromManifest(this, connectorId);

    Task<McpConnectorSession> AcquireAsync(string connectorId, CancellationToken ct = default);

    /// <summary>Drops the current session and initializes a fresh one. The new
    /// session must carry a higher generation so answers meant for the old one
    /// can never be honoured.</summary>
    Task<McpConnectorSession> ReconnectAsync(string connectorId, CancellationToken ct = default);

    Task ReleaseAsync(string connectorId);
}

/// <summary>Rebuilds a resolution from a manifest, for providers that hold
/// approved manifests directly rather than resolving them from disk.</summary>
public static class McpConnectorResolution
{
    public static ConnectorResolution FromManifest(
        IMcpConnectorSessionProvider provider,
        string connectorId)
    {
        var manifest = provider.GetManifest(connectorId);
        if (manifest is null)
            return ConnectorResolution.Deny(
                "ConnectorNotInstalled",
                $"No connector '{connectorId}' is installed.",
                ConnectorTrustState.Untrusted);
        // The trust verdict is deliberately NOT computed here: this overload has
        // no installation policy, and evaluating with the default one would
        // reject a connector the gateway would have allowed — or, worse, name the
        // wrong reason for a refusal. The gateway evaluates with the policy it
        // was composed with, immediately after this returns.
        return ConnectorResolution.Allow(new ConnectorLaunchDescriptor(
            manifest,
            new ConnectorDeclaration
            {
                ConnectorId = manifest.ConnectorId,
                Name = manifest.Name,
                Version = manifest.Version,
                Publisher = manifest.Publisher,
                Transport = manifest.Transport,
                ExecutablePath = manifest.ExecutablePath,
                ServerUrl = manifest.ServerUrl,
                InstallSource = manifest.InstallSource
            },
            new ConnectorTrustRecord
            {
                ConnectorId = manifest.ConnectorId,
                Version = manifest.Version,
                Transport = manifest.Transport,
                ExecutableSha256 = manifest.ExecutableSha256,
                ExecutablePath = manifest.ExecutablePath,
                State = manifest.TrustState,
                Enabled = manifest.Enabled
            },
            provider.GetRiskPolicy(connectorId)));
    }
}
