using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>How a connector's server is reached. The distinction matters for
/// trust: a stdio server is a binary on this machine whose identity is its
/// hash, while an HTTP server is an endpoint whose identity is its URL and the
/// credential it is handed.</summary>
public enum ConnectorTransportKind
{
    Stdio,
    Http
}

/// <summary>
/// Everything a connector asks to be allowed to do. Kept as one value so an
/// approval can be compared against a later request as a whole: a connector
/// that gains network access, a workspace mount, or a credential after the fact
/// is asking for a NEW decision, and comparing field by field at each call site
/// is exactly how one of them gets forgotten.
/// </summary>
public sealed record ConnectorPermissionSet
{
    public IReadOnlyList<string> Capabilities { get; init; } = [];
    public IReadOnlyList<string> WorkspaceAccess { get; init; } = [];
    public bool RequiresNetwork { get; init; }
    public IReadOnlyList<string> CredentialRequirements { get; init; } = [];

    public static ConnectorPermissionSet Empty { get; } = new();

    /// <summary>Permissions present in this set that <paramref name="approved"/>
    /// does not cover. Empty means the request fits inside the decision.</summary>
    public IReadOnlyList<string> ExpansionsOver(ConnectorPermissionSet approved)
    {
        ArgumentNullException.ThrowIfNull(approved);
        var expansions = new List<string>();
        expansions.AddRange(Capabilities
            .Where(item => !approved.Capabilities.Contains(item, StringComparer.OrdinalIgnoreCase))
            .Select(item => $"capability:{item}"));
        expansions.AddRange(WorkspaceAccess
            .Where(item => !approved.WorkspaceAccess.Contains(item, StringComparer.OrdinalIgnoreCase))
            .Select(item => $"workspace:{item}"));
        if (RequiresNetwork && !approved.RequiresNetwork)
            expansions.Add("network");
        expansions.AddRange(CredentialRequirements
            .Where(item => !approved.CredentialRequirements.Contains(
                item, StringComparer.OrdinalIgnoreCase))
            .Select(item => $"credential:{item}"));
        return expansions;
    }

    public string Fingerprint() =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(
            "|",
            string.Join(",", Capabilities.OrderBy(item => item, StringComparer.Ordinal)),
            string.Join(",", WorkspaceAccess.OrderBy(item => item, StringComparer.Ordinal)),
            RequiresNetwork ? "network" : "no-network",
            string.Join(",",
                CredentialRequirements.OrderBy(item => item, StringComparer.Ordinal))))))[..32];
}

/// <summary>
/// A connector manifest exactly as it was found on disk (or, for a marketplace
/// HTTP connector, as it was derived from the install record). It is a
/// DECLARATION, not a decision: nothing here has been approved, and the resolver
/// deliberately produces one for connectors it is about to refuse so the UI can
/// show the user what is being asked for.
/// </summary>
public sealed record ConnectorDeclaration
{
    public required string ConnectorId { get; init; }
    public required string Name { get; init; }
    public required string Version { get; init; }
    public string? Publisher { get; init; }
    public string Description { get; init; } = "";

    public ConnectorTransportKind Transport { get; init; } = ConnectorTransportKind.Stdio;

    /// <summary>stdio: the canonicalised absolute path of the server executable.</summary>
    public string ExecutablePath { get; init; } = "";

    /// <summary>http: the endpoint the server lives at.</summary>
    public string ServerUrl { get; init; } = "";

    public string InstallSource { get; init; } = "user-supplied";
    public string ProtocolVersion { get; init; } = "2025-06-18";

    /// <summary>Absolute path of the manifest file itself. Empty for a derived
    /// (marketplace) declaration, which has no file.</summary>
    public string ManifestPath { get; init; } = "";

    public ConnectorPermissionSet Permissions { get; init; } = ConnectorPermissionSet.Empty;

    public IReadOnlyList<string> ToolCapabilities { get; init; } = [];
    public IReadOnlyList<string> ResourceCapabilities { get; init; } = [];
    public IReadOnlyList<string> PromptCapabilities { get; init; } = [];

    /// <summary>Human-readable summary of what the riskiest tools do. Shown at
    /// approval time; it never influences classification, which comes from the
    /// approved risk policy.</summary>
    public string RiskSummary { get; init; } = "";

    public IReadOnlyList<string> ReadOnlyTools { get; init; } = [];
    public IReadOnlyList<string> LocalMutationTools { get; init; } = [];
    public IReadOnlyList<string> ExternalMutationTools { get; init; } = [];
    public IReadOnlyList<string> DestructiveTools { get; init; } = [];
    public IReadOnlyDictionary<string, string> VerificationTools { get; init; } =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

    public DateTimeOffset CreatedAtUtc { get; init; } = DateTimeOffset.UtcNow;

    /// <summary>Identity of the thing that will actually run. For stdio it is the
    /// executable's SHA-256; for HTTP there is no binary, so it is the endpoint
    /// plus the credential mode — the two things that decide where the request
    /// goes and what it carries.</summary>
    public string EndpointFingerprint() =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(
            "|",
            Transport.ToString(),
            ServerUrl.ToLowerInvariant(),
            ExecutablePath.ToLowerInvariant()))))[..32];

    /// <summary>Everything that constitutes the declaration, hashed. Timestamps
    /// are excluded so re-reading the same file does not invalidate it.</summary>
    public string ComputeFingerprint(string executableSha256) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(
            "|",
            ConnectorId,
            Name,
            Version,
            Publisher ?? "",
            Transport.ToString(),
            ExecutablePath.ToLowerInvariant(),
            ServerUrl.ToLowerInvariant(),
            (executableSha256 ?? "").ToUpperInvariant(),
            InstallSource,
            ProtocolVersion,
            Permissions.Fingerprint(),
            string.Join(",", ReadOnlyTools.OrderBy(item => item, StringComparer.Ordinal)),
            string.Join(",", LocalMutationTools.OrderBy(item => item, StringComparer.Ordinal)),
            string.Join(",", ExternalMutationTools.OrderBy(item => item, StringComparer.Ordinal)),
            string.Join(",", DestructiveTools.OrderBy(item => item, StringComparer.Ordinal))))))[..32];
}

/// <summary>
/// The persisted decision. It names the exact thing that was approved — this
/// connector, at this version, with this binary, declaring this manifest, asking
/// for these permissions — so that anything else on disk later is a different
/// thing that has to be approved again.
///
/// Stored OUTSIDE the connector installation root on purpose: a server that
/// could write its own trust record would only need one launch to grant itself
/// everything.
/// </summary>
public sealed record ConnectorTrustRecord
{
    public required string ConnectorId { get; init; }
    public required string Version { get; init; }
    public ConnectorTransportKind Transport { get; init; } = ConnectorTransportKind.Stdio;

    /// <summary>SHA-256 of the approved executable. Empty for HTTP, which has no
    /// binary — and empty for a legacy record, which is why such a record can
    /// never be trusted without a new approval.</summary>
    public string ExecutableSha256 { get; init; } = "";

    public string ExecutablePath { get; init; } = "";
    public string EndpointFingerprint { get; init; } = "";
    public string ManifestFingerprint { get; init; } = "";
    public string? Publisher { get; init; }
    public string InstallSource { get; init; } = "";

    public ConnectorPermissionSet ApprovedPermissions { get; init; } =
        ConnectorPermissionSet.Empty;

    /// <summary>Who made the decision. Never a model, never a server: the values
    /// this can hold are user identities and administrative policy names.</summary>
    public string ApprovedBy { get; init; } = "";

    public DateTimeOffset? ApprovedAtUtc { get; init; }
    public bool Enabled { get; init; }
    public DateTimeOffset? RevokedAtUtc { get; init; }
    public DateTimeOffset? LastValidatedUtc { get; init; }
    public ConnectorTrustState State { get; init; } = ConnectorTrustState.Untrusted;

    /// <summary>Why the record is in its current state — carried so the UI can
    /// explain a re-approval prompt instead of just showing a blocked button.</summary>
    public string StateReason { get; init; } = "";

    [JsonIgnore]
    public bool IsApproved => State == ConnectorTrustState.Trusted && Enabled;
}

/// <summary>Everything needed to launch an approved connector, produced only
/// after the resolver has re-verified identity against the stored decision.</summary>
public sealed record ConnectorLaunchDescriptor(
    ConnectorManifest Manifest,
    ConnectorDeclaration Declaration,
    ConnectorTrustRecord Trust,
    Services.ConnectorRiskPolicy RiskPolicy);

/// <summary>
/// The resolver's answer. Either a launch descriptor or a named refusal —
/// never null-meaning-something, and never a descriptor built from a connector
/// id alone.
/// </summary>
public sealed record ConnectorResolution
{
    public ConnectorLaunchDescriptor? Descriptor { get; init; }

    /// <summary>Present even on refusal when the declaration could be read, so
    /// the approval UI can show what is being asked for.</summary>
    public ConnectorDeclaration? Declaration { get; init; }

    public ConnectorTrustRecord? Trust { get; init; }
    public ConnectorTrustState State { get; init; } = ConnectorTrustState.Untrusted;
    public string? DenialCode { get; init; }
    public string? DenialReason { get; init; }

    /// <summary>Permissions the declaration asks for that the decision does not
    /// cover. Non-empty means re-approval, not a wider grant.</summary>
    public IReadOnlyList<string> PermissionExpansions { get; init; } = [];

    [JsonIgnore]
    public bool Resolved => Descriptor is not null;

    public static ConnectorResolution Allow(ConnectorLaunchDescriptor descriptor) =>
        new()
        {
            Descriptor = descriptor,
            Declaration = descriptor.Declaration,
            Trust = descriptor.Trust,
            State = ConnectorTrustState.Trusted
        };

    public static ConnectorResolution Deny(
        string code,
        string reason,
        ConnectorTrustState state,
        ConnectorDeclaration? declaration = null,
        ConnectorTrustRecord? trust = null,
        IReadOnlyList<string>? expansions = null) => new()
    {
        DenialCode = code,
        DenialReason = reason,
        State = state,
        Declaration = declaration,
        Trust = trust,
        PermissionExpansions = expansions ?? []
    };
}

/// <summary>What the approval UI is being asked to decide about.</summary>
public sealed record ConnectorApprovalRequest(
    ConnectorDeclaration Declaration,
    string ExecutableSha256,
    string ManifestFingerprint,
    ConnectorTrustRecord? Existing,
    IReadOnlyList<string> PermissionExpansions,
    string? PreviousDenialCode = null);

public enum ConnectorApprovalOutcome
{
    Denied,
    Approved,
    ApprovedForThisVersion,
    Disabled,
    Revoked
}

public sealed record ConnectorApprovalDecision(
    ConnectorApprovalOutcome Outcome,
    string ApprovedBy,
    string? Reason = null)
{
    public static ConnectorApprovalDecision Deny(string reason) =>
        new(ConnectorApprovalOutcome.Denied, "", reason);
}
