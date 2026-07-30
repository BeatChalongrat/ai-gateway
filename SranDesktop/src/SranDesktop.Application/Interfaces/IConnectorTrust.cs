using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public sealed record ConnectorTrustChange(
    string ConnectorId,
    ConnectorTrustState State,
    string Reason);

/// <summary>
/// Where connector approvals live. Deliberately a store rather than a field on
/// the connector's own install record: the record sits next to the connector's
/// files, and a server that can rewrite the thing that authorises it needs only
/// one launch to authorise itself.
/// </summary>
public interface IConnectorTrustStore
{
    ConnectorTrustRecord? Get(string connectorId);

    IReadOnlyList<ConnectorTrustRecord> All();

    Task<ConnectorTrustRecord> SaveAsync(
        ConnectorTrustRecord record,
        CancellationToken ct = default);

    Task RemoveAsync(string connectorId, CancellationToken ct = default);

    /// <summary>Raised after a decision changes so live sessions for a connector
    /// that just lost its trust can be torn down rather than kept running.</summary>
    event EventHandler<ConnectorTrustChange>? TrustChanged;
}

/// <summary>
/// Turns "connector id" into either a launch descriptor or a named refusal.
///
/// It never returns a descriptor for an id alone: the manifest is re-read, the
/// executable re-hashed, and the result compared against the stored decision on
/// every resolution, because the interesting failures — a replaced binary, a
/// widened manifest, a connector pointed somewhere else — all happen between
/// approval and launch.
/// </summary>
public interface IConnectorManifestResolver
{
    /// <summary>Every connector installed in the controlled location, whether or
    /// not it is approved. This is the discovery half of the approval flow.</summary>
    IReadOnlyList<ConnectorDeclaration> Discover();

    /// <summary>The declaration alone, with no trust decision applied. For the
    /// approval UI, which must be able to show what an unapproved connector is
    /// asking for.</summary>
    ConnectorDeclaration? ReadDeclaration(string connectorId);

    ConnectorResolution Resolve(string connectorId);
}

/// <summary>
/// Records approvals. Every method here requires evidence that a person acted:
/// there is no overload that takes a decision from a model, from a tool result,
/// or from anything a connector said.
/// </summary>
public interface IConnectorTrustAdministration
{
    Task<ConnectorTrustRecord> ApproveAsync(
        string connectorId,
        ConnectorApprovalDecision decision,
        UserApprovalEvidence evidence,
        CancellationToken ct = default);

    Task<ConnectorTrustRecord?> DisableAsync(
        string connectorId,
        UserApprovalEvidence evidence,
        CancellationToken ct = default);

    Task<ConnectorTrustRecord?> RevokeAsync(
        string connectorId,
        UserApprovalEvidence evidence,
        CancellationToken ct = default);
}

/// <summary>
/// Proof that a human made the decision.
///
/// It cannot be constructed from arbitrary code: the only way to get one is
/// <see cref="FromUserGesture"/>, which refuses while the agent's own synthetic
/// input is in flight. That is the concrete attack — the agent has a click tool
/// and an approval dialog is a clickable window — and a policy that lives only
/// in a comment does not stop it.
/// </summary>
public sealed record UserApprovalEvidence
{
    private UserApprovalEvidence(string principal, string gesture, DateTimeOffset atUtc)
    {
        Principal = principal;
        Gesture = gesture;
        AtUtc = atUtc;
    }

    public string Principal { get; }
    public string Gesture { get; }
    public DateTimeOffset AtUtc { get; }

    /// <summary>A decision taken by the signed-in user at the keyboard.</summary>
    public static UserApprovalEvidence FromUserGesture(string principal, string gesture)
    {
        if (string.IsNullOrWhiteSpace(principal))
            throw new ArgumentException(
                "An approval must name the person who made it.", nameof(principal));
        if (Services.AgentInputScope.IsAgentInputInFlight(out var reason))
            throw new UnauthorizedAccessException(
                $"A connector approval cannot be taken while the agent is driving the "
                + $"user interface ({reason}). A person has to make this decision.");
        return new UserApprovalEvidence(principal, gesture, DateTimeOffset.UtcNow);
    }

    /// <summary>An approval that came from administrative policy rather than a
    /// live gesture — a managed deployment pre-approving its own connectors.
    /// Named separately so it is visible in the audit trail as what it is.</summary>
    public static UserApprovalEvidence FromAdministrativePolicy(string policyName)
    {
        if (string.IsNullOrWhiteSpace(policyName))
            throw new ArgumentException(
                "An administrative approval must name its policy.", nameof(policyName));
        return new UserApprovalEvidence(
            $"policy:{policyName}", "administrative-policy", DateTimeOffset.UtcNow);
    }
}
