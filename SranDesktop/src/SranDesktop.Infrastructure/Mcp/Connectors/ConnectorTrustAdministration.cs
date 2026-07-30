using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Mcp.Connectors;

/// <summary>
/// The only place a connector becomes trusted.
///
/// Every entry point demands <see cref="UserApprovalEvidence"/>, which cannot be
/// manufactured while the agent is driving the UI. That is the whole defence
/// against the obvious attack: the agent has a click tool, an approval dialog is
/// a window with an Approve button, and a policy written only in prose would not
/// stop it from pressing that button.
///
/// The identity and the fingerprint are computed HERE, at the moment of the
/// decision, from the declaration the user was shown. Recomputing them later
/// would approve whatever is on disk then; taking them from the caller would
/// approve whatever the caller claimed.
/// </summary>
public sealed class ConnectorTrustAdministration : IConnectorTrustAdministration
{
    private readonly IConnectorManifestResolver _resolver;
    private readonly IConnectorTrustStore _store;
    private readonly ConnectorInstallationPolicy _installation;
    private readonly ILogger? _logger;

    public ConnectorTrustAdministration(
        IConnectorManifestResolver resolver,
        IConnectorTrustStore store,
        ConnectorInstallationPolicy? installation = null,
        ILogger? logger = null)
    {
        _resolver = resolver;
        _store = store;
        _installation = installation ?? ConnectorInstallationPolicy.Default;
        _logger = logger;
    }

    public async Task<ConnectorTrustRecord> ApproveAsync(
        string connectorId,
        ConnectorApprovalDecision decision,
        UserApprovalEvidence evidence,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(decision);
        ArgumentNullException.ThrowIfNull(evidence);

        var declaration = _resolver.ReadDeclaration(connectorId)
                          ?? throw new InvalidOperationException(
                              $"No connector '{connectorId}' is installed, so there is "
                              + "nothing to approve.");

        if (decision.Outcome is ConnectorApprovalOutcome.Denied)
            return await RecordAsync(
                Deny(declaration, evidence, decision.Reason), ct);
        if (decision.Outcome is ConnectorApprovalOutcome.Disabled)
            return await DisableAsync(connectorId, evidence, ct)
                   ?? throw new InvalidOperationException("The connector has no trust record.");
        if (decision.Outcome is ConnectorApprovalOutcome.Revoked)
            return await RevokeAsync(connectorId, evidence, ct)
                   ?? throw new InvalidOperationException("The connector has no trust record.");

        if (declaration.Transport == ConnectorTransportKind.Stdio)
        {
            if (!_installation.Contains(declaration.ExecutablePath))
                throw new InvalidOperationException(
                    "A connector outside the managed installation location cannot be "
                    + "approved: its executable could be replaced between the decision "
                    + "and the launch.");
            if (!File.Exists(declaration.ExecutablePath))
                throw new FileNotFoundException(
                    "The connector executable does not exist.", declaration.ExecutablePath);
        }

        var executableHash = declaration.Transport == ConnectorTransportKind.Stdio
            ? ConnectorTrustPolicy.HashExecutable(declaration.ExecutablePath)
            : "";

        var record = new ConnectorTrustRecord
        {
            ConnectorId = declaration.ConnectorId,
            Version = declaration.Version,
            Transport = declaration.Transport,
            ExecutableSha256 = executableHash,
            ExecutablePath = declaration.ExecutablePath,
            EndpointFingerprint = declaration.EndpointFingerprint(),
            ManifestFingerprint = declaration.ComputeFingerprint(executableHash),
            Publisher = declaration.Publisher,
            InstallSource = declaration.InstallSource,
            // Approving grants exactly what was on the screen. Anything the
            // connector asks for later is a new decision, which is what makes
            // "permission expansion invalidates trust" work at all.
            ApprovedPermissions = declaration.Permissions,
            ApprovedBy = evidence.Principal,
            ApprovedAtUtc = evidence.AtUtc,
            Enabled = true,
            RevokedAtUtc = null,
            State = ConnectorTrustState.Trusted,
            StateReason = decision.Outcome == ConnectorApprovalOutcome.ApprovedForThisVersion
                ? $"Approved for version {declaration.Version} via {evidence.Gesture}."
                : $"Approved via {evidence.Gesture}."
        };

        _logger?.LogInformation(
            "Connector {Id} v{Version} approved by {By} ({Gesture})",
            declaration.ConnectorId, declaration.Version, evidence.Principal, evidence.Gesture);
        return await RecordAsync(record, ct);
    }

    public async Task<ConnectorTrustRecord?> DisableAsync(
        string connectorId,
        UserApprovalEvidence evidence,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(evidence);
        if (_store.Get(connectorId) is not { } existing)
            return null;
        return await RecordAsync(existing with
        {
            Enabled = false,
            StateReason = $"Disabled by {evidence.Principal}."
        }, ct);
    }

    public async Task<ConnectorTrustRecord?> RevokeAsync(
        string connectorId,
        UserApprovalEvidence evidence,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(evidence);
        if (_store.Get(connectorId) is not { } existing)
            return null;
        // The approved identity is deliberately kept: an audit later has to be
        // able to say WHAT was trusted, and clearing it would also let the same
        // binary be re-approved without the user seeing that it is the one whose
        // trust they just withdrew.
        return await RecordAsync(existing with
        {
            State = ConnectorTrustState.Revoked,
            Enabled = false,
            RevokedAtUtc = DateTimeOffset.UtcNow,
            StateReason = $"Revoked by {evidence.Principal}."
        }, ct);
    }

    /// <summary>
    /// Brings a connector that predates trust records forward. It lands in
    /// <see cref="ConnectorTrustState.AwaitingTrust"/> and stays unrunnable: the
    /// tempting shortcut — hash what is on disk and call it approved — would
    /// grant trust to whatever binary happens to be there, which is precisely
    /// the thing an attacker who got write access is hoping for.
    /// </summary>
    public async Task<ConnectorTrustRecord> MigrateLegacyAsync(
        ConnectorDeclaration declaration,
        string reason,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(declaration);
        if (_store.Get(declaration.ConnectorId) is { } existing)
            return existing;
        _logger?.LogInformation(
            "Connector {Id} predates trust records; it needs approval before it can run",
            declaration.ConnectorId);
        return await RecordAsync(new ConnectorTrustRecord
        {
            ConnectorId = declaration.ConnectorId,
            Version = declaration.Version,
            Transport = declaration.Transport,
            ExecutableSha256 = "",
            ExecutablePath = declaration.ExecutablePath,
            EndpointFingerprint = "",
            ManifestFingerprint = "",
            Publisher = declaration.Publisher,
            InstallSource = declaration.InstallSource,
            ApprovedPermissions = ConnectorPermissionSet.Empty,
            ApprovedBy = "",
            ApprovedAtUtc = null,
            Enabled = false,
            State = ConnectorTrustState.AwaitingTrust,
            StateReason = reason
        }, ct);
    }

    private ConnectorTrustRecord Deny(
        ConnectorDeclaration declaration,
        UserApprovalEvidence evidence,
        string? reason) => new()
    {
        ConnectorId = declaration.ConnectorId,
        Version = declaration.Version,
        Transport = declaration.Transport,
        ExecutablePath = declaration.ExecutablePath,
        Publisher = declaration.Publisher,
        InstallSource = declaration.InstallSource,
        ApprovedPermissions = ConnectorPermissionSet.Empty,
        Enabled = false,
        State = ConnectorTrustState.Untrusted,
        StateReason = reason is { Length: > 0 }
            ? $"Denied by {evidence.Principal}: {reason}"
            : $"Denied by {evidence.Principal}."
    };

    private Task<ConnectorTrustRecord> RecordAsync(
        ConnectorTrustRecord record,
        CancellationToken ct) => _store.SaveAsync(record, ct);
}
