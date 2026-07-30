using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Mcp.Connectors;

/// <summary>
/// A connector the user is trying out before installing it — the "Test
/// connection" button in the add-connector dialog.
///
/// The grant exists because the alternative is worse. Dialling an unsaved
/// definition outside the gateway would be an MCP operation with no AgentStep,
/// no recorded intent, and no observation boundary, which is exactly the bypass
/// this phase removes. So the test dial goes down the same road as everything
/// else, and this is the narrow, explicitly-scoped way a not-yet-installed
/// connector gets far enough to be dialled.
///
/// Three properties keep it from being a hole:
/// it requires <see cref="UserApprovalEvidence"/>, so the agent cannot mint one;
/// it lives in memory for the duration of one call and is removed in a finally
/// block, so it cannot be a persistent approval; and it refuses local
/// executables outright, because a binary that has not been installed into the
/// controlled location and approved must never run, however briefly.
/// </summary>
public sealed class TransientConnectorGrants
{
    private readonly object _gate = new();
    private readonly Dictionary<string, Entry> _grants = new(StringComparer.Ordinal);

    /// <summary>
    /// Opens a grant for one connector for as long as the returned handle lives.
    /// </summary>
    /// <exception cref="InvalidOperationException">The declaration names a local
    /// executable. An unapproved binary does not get to run as a "test".</exception>
    public IDisposable Grant(ConnectorDeclaration declaration, UserApprovalEvidence evidence)
    {
        ArgumentNullException.ThrowIfNull(declaration);
        ArgumentNullException.ThrowIfNull(evidence);
        if (declaration.Transport != ConnectorTransportKind.Http)
            throw new InvalidOperationException(
                "A local connector cannot be tested before it is installed and approved: "
                + "running the executable IS the risk the approval exists to gate.");

        var record = new ConnectorTrustRecord
        {
            ConnectorId = declaration.ConnectorId,
            Version = declaration.Version,
            Transport = declaration.Transport,
            ExecutableSha256 = "",
            ExecutablePath = "",
            EndpointFingerprint = declaration.EndpointFingerprint(),
            ManifestFingerprint = declaration.ComputeFingerprint(""),
            Publisher = declaration.Publisher,
            InstallSource = "test-connection",
            ApprovedPermissions = declaration.Permissions,
            ApprovedBy = evidence.Principal,
            ApprovedAtUtc = evidence.AtUtc,
            Enabled = true,
            State = ConnectorTrustState.Trusted,
            StateReason =
                $"Temporary grant for a pre-install connection test ({evidence.Gesture}). "
                + "Not persisted."
        };

        lock (_gate)
            _grants[declaration.ConnectorId] = new Entry(declaration, record);
        return new Handle(this, declaration.ConnectorId);
    }

    public ConnectorResolution? TryResolve(string connectorId)
    {
        Entry? entry;
        lock (_gate)
            entry = _grants.GetValueOrDefault(connectorId);
        if (entry is null)
            return null;

        var manifest = ConnectorManifestResolver.ToManifest(
            entry.Declaration, entry.Record, "", entry.Record.ManifestFingerprint);
        return ConnectorResolution.Allow(new ConnectorLaunchDescriptor(
            manifest,
            entry.Declaration,
            entry.Record,
            ConnectorManifestResolver.ToRiskPolicy(entry.Declaration)));
    }

    public IReadOnlyList<ConnectorDeclaration> Declarations()
    {
        lock (_gate)
            return _grants.Values.Select(entry => entry.Declaration).ToList();
    }

    private void Release(string connectorId)
    {
        lock (_gate)
            _grants.Remove(connectorId);
    }

    private sealed record Entry(ConnectorDeclaration Declaration, ConnectorTrustRecord Record);

    private sealed class Handle(TransientConnectorGrants owner, string connectorId) : IDisposable
    {
        private bool _released;

        public void Dispose()
        {
            if (_released)
                return;
            _released = true;
            owner.Release(connectorId);
        }
    }
}
