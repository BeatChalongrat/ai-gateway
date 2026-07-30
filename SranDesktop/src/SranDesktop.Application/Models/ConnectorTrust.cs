using System.Security.Cryptography;
using System.Text;

namespace SranDesktop.Application.Models;

public enum ConnectorTrustState
{
    /// <summary>Discovered but never approved. Cannot run.</summary>
    Untrusted,

    /// <summary>Installed before executable identity was recorded, or its
    /// recorded identity is gone. Cannot run until the user approves it again —
    /// it is deliberately NOT the same thing as Untrusted, so the UI can say
    /// "this used to work, please re-approve" instead of losing the install.</summary>
    AwaitingTrust,

    Trusted,

    /// <summary>Was trusted, but what is on disk no longer matches what was
    /// approved. Cannot run until re-approved.</summary>
    Invalidated,

    Revoked
}

/// <summary>
/// Where a local connector executable is allowed to live. Trusting a hash is not
/// enough on its own: a binary sitting in the downloads folder or a world-writable
/// temp directory can be replaced between the check and the launch, so the
/// approved location has to be one the user cannot be tricked into writing to by
/// a drive-by download.
/// </summary>
public sealed record ConnectorInstallationPolicy(IReadOnlyList<string> ControlledRoots)
{
    /// <summary>Installation roots for a real deployment: the application's own
    /// directory and the per-user connector store it manages.</summary>
    public static ConnectorInstallationPolicy Default { get; } = new(
    [
        AppContext.BaseDirectory,
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "SRAN Desktop",
            "connectors")
    ]);

    public bool Contains(string executablePath)
    {
        if (string.IsNullOrWhiteSpace(executablePath) || ControlledRoots.Count == 0)
            return false;
        string full;
        try
        {
            full = Path.GetFullPath(executablePath);
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException
                                       or PathTooLongException)
        {
            return false;
        }
        return ControlledRoots.Any(root =>
        {
            var normalized = Path.GetFullPath(root)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                             + Path.DirectorySeparatorChar;
            return full.StartsWith(normalized, StringComparison.OrdinalIgnoreCase);
        });
    }
}

/// <summary>
/// What the user actually approved when they trusted a local connector. The
/// executable hash is part of that decision: trusting "the thing at this path"
/// would let the binary be swapped afterwards and inherit the approval. So is
/// the permission set — a connector that later asks for the network or a wider
/// workspace is asking for a NEW decision, not exercising the old one.
/// </summary>
public sealed record ConnectorManifest
{
    public required string ConnectorId { get; init; }
    public required string Name { get; init; }
    public required string Version { get; init; }
    public string? Publisher { get; init; }

    /// <summary>How the server is reached. A stdio connector's identity is the
    /// hash of the binary that will run; an HTTP connector has no binary, so its
    /// identity is the endpoint it will be dialled at.</summary>
    public ConnectorTransportKind Transport { get; init; } = ConnectorTransportKind.Stdio;

    /// <summary>http only: the approved endpoint. Changing it is a different
    /// destination for the same name, which is a new decision.</summary>
    public string ServerUrl { get; init; } = "";

    /// <summary>http only: fingerprint of the endpoint as approved.</summary>
    public string? ApprovedEndpointFingerprint { get; init; }

    /// <summary>Protocol version the declaration was written against.</summary>
    public string ProtocolVersion { get; init; } = "2025-06-18";

    /// <summary>Who approved it. Never a model and never the server.</summary>
    public string ApprovedBy { get; init; } = "";

    /// <summary>Absolute path of the server executable. Recorded at trust time and
    /// never taken from a model or server response.</summary>
    public required string ExecutablePath { get; init; }

    /// <summary>SHA-256 of the executable as it was when trust was granted. Empty
    /// means the identity was never recorded — a legacy install, which must be
    /// re-approved rather than auto-trusted.</summary>
    public required string ExecutableSha256 { get; init; }

    public string InstallSource { get; init; } = "user-supplied";

    /// <summary>What the manifest asks for NOW.</summary>
    public IReadOnlyList<string> RequestedCapabilities { get; init; } = [];

    /// <summary>What the user approved. A capability in Requested but not here is
    /// an expansion and blocks the launch.</summary>
    public IReadOnlyList<string> ApprovedCapabilities { get; init; } = [];

    public IReadOnlyList<string> WorkspaceAccess { get; init; } = [];
    public bool RequiresNetwork { get; init; }
    public IReadOnlyList<string> CredentialRequirements { get; init; } = [];

    /// <summary>Fingerprint of the manifest as approved. Recomputed at launch; a
    /// difference means the declaration changed after the decision.</summary>
    public string? ApprovedManifestFingerprint { get; init; }

    public ConnectorTrustState TrustState { get; init; } = ConnectorTrustState.Untrusted;
    public bool Enabled { get; init; }
    public DateTimeOffset? TrustedAtUtc { get; init; }
    public DateTimeOffset? LastVerifiedUtc { get; init; }

    /// <summary>Everything that constitutes the approved declaration. Trust state
    /// and timestamps are excluded on purpose: re-verifying must not itself
    /// invalidate the manifest.</summary>
    public string ComputeManifestFingerprint() =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(
            "",
            ConnectorId,
            Name,
            Version,
            Publisher ?? "",
            Transport.ToString(),
            ServerUrl.ToLowerInvariant(),
            ExecutablePath.ToLowerInvariant(),
            ExecutableSha256.ToUpperInvariant(),
            InstallSource,
            string.Join(",", RequestedCapabilities.OrderBy(item => item, StringComparer.Ordinal)),
            string.Join(",", WorkspaceAccess.OrderBy(item => item, StringComparer.Ordinal)),
            RequiresNetwork ? "network" : "no-network",
            string.Join(",",
                CredentialRequirements.OrderBy(item => item, StringComparer.Ordinal))))))[..32];

    /// <summary>Where this connector's traffic actually goes. For HTTP that is
    /// the whole of its identity — there is no binary to hash.</summary>
    public string ComputeEndpointFingerprint() =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join(
            "|",
            Transport.ToString(),
            ServerUrl.ToLowerInvariant(),
            ExecutablePath.ToLowerInvariant()))))[..32];
}

public sealed record ConnectorTrustVerdict(
    bool MayLaunch,
    ConnectorTrustState State,
    string? RejectionCode,
    string? Reason);

/// <summary>
/// Decides whether a local connector may be launched at all. Everything here is
/// deliberately fail-closed: an unreadable executable, a changed hash, an
/// unrecorded identity, a path outside the controlled install location, a
/// changed manifest, or a widened permission set all mean "do not run", never
/// "run anyway".
/// </summary>
public static class ConnectorTrustPolicy
{
    public static ConnectorTrustVerdict Evaluate(
        ConnectorManifest manifest,
        ConnectorInstallationPolicy? installation = null)
    {
        ArgumentNullException.ThrowIfNull(manifest);
        var policy = installation ?? ConnectorInstallationPolicy.Default;

        if (manifest.TrustState == ConnectorTrustState.Revoked)
            return Refuse(ConnectorTrustState.Revoked, "ConnectorRevoked",
                "Trust for this connector was revoked.");
        if (!manifest.Enabled)
            return Refuse(manifest.TrustState, "ConnectorDisabled",
                "The connector is installed but not enabled.");

        // A legacy install with no recorded identity is not "probably fine": we
        // have no idea what binary was approved, so there is nothing to compare
        // against and nothing to inherit. The HTTP equivalent is an install with
        // no approved endpoint — the same hole, one layer up: without it, any URL
        // could inherit the decision made about a different one.
        var identityMissing = manifest.Transport == ConnectorTransportKind.Http
            ? string.IsNullOrWhiteSpace(manifest.ApprovedEndpointFingerprint)
            : string.IsNullOrWhiteSpace(manifest.ExecutableSha256);
        if (identityMissing)
            return Refuse(ConnectorTrustState.AwaitingTrust, "ConnectorIdentityUnknown",
                manifest.Transport == ConnectorTransportKind.Http
                    ? "No endpoint identity was recorded for this connector, so the approval "
                      + "cannot be tied to a specific server. It must be approved again."
                    : "No executable identity was recorded for this connector, so the approval "
                      + "cannot be tied to a specific binary. It must be approved again.");

        if (manifest.TrustState == ConnectorTrustState.AwaitingTrust)
            return Refuse(ConnectorTrustState.AwaitingTrust, "ConnectorAwaitingTrust",
                "The connector is waiting for the user to approve it again.");
        if (manifest.TrustState != ConnectorTrustState.Trusted)
            return Refuse(manifest.TrustState, "ConnectorUntrusted",
                "The connector has not been approved by the user.");

        // Only a local binary has a location that can be tampered with between
        // the check and the launch; an HTTP endpoint's equivalent is its URL,
        // checked below against the one that was approved.
        if (manifest.Transport == ConnectorTransportKind.Stdio
            && !policy.Contains(manifest.ExecutablePath))
            return Refuse(ConnectorTrustState.Invalidated, "ConnectorPathNotControlled",
                "The executable is outside the managed installation location, where it "
                + "could be replaced between the check and the launch.");

        if (manifest.ApprovedManifestFingerprint is { Length: > 0 } approvedFingerprint
            && !string.Equals(
                approvedFingerprint,
                manifest.ComputeManifestFingerprint(),
                StringComparison.OrdinalIgnoreCase))
            return Refuse(ConnectorTrustState.Invalidated, "ConnectorManifestChanged",
                "The connector's declaration changed after it was approved.");

        var expanded = manifest.RequestedCapabilities
            .Where(capability => !manifest.ApprovedCapabilities.Contains(
                capability, StringComparer.OrdinalIgnoreCase))
            .ToList();
        if (expanded.Count > 0)
            return Refuse(ConnectorTrustState.Invalidated, "ConnectorPermissionExpanded",
                $"The connector now asks for permissions that were never approved: "
                + $"{string.Join(", ", expanded)}.");

        if (manifest.Transport == ConnectorTransportKind.Http)
        {
            if (!string.Equals(
                    manifest.ApprovedEndpointFingerprint,
                    manifest.ComputeEndpointFingerprint(),
                    StringComparison.OrdinalIgnoreCase))
                // Same connector name, different destination. The user approved a
                // server, not a label.
                return Refuse(ConnectorTrustState.Invalidated, "ConnectorEndpointChanged",
                    "The connector now points at a different server than the one that "
                    + "was approved.");
            return new ConnectorTrustVerdict(true, ConnectorTrustState.Trusted, null, null);
        }

        string actualHash;
        try
        {
            if (!File.Exists(manifest.ExecutablePath))
                return Refuse(ConnectorTrustState.Invalidated, "ConnectorExecutableMissing",
                    "The approved executable is no longer present.");
            using var stream = File.OpenRead(manifest.ExecutablePath);
            actualHash = Convert.ToHexString(SHA256.HashData(stream));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return Refuse(ConnectorTrustState.Invalidated, "ConnectorExecutableUnreadable",
                ex.Message);
        }

        if (!string.Equals(actualHash, manifest.ExecutableSha256, StringComparison.OrdinalIgnoreCase))
            // The user approved a specific binary; this is a different one.
            return Refuse(ConnectorTrustState.Invalidated, "ConnectorExecutableChanged",
                "The executable changed after it was trusted and must be approved again.");

        return new ConnectorTrustVerdict(true, ConnectorTrustState.Trusted, null, null);
    }

    /// <summary>
    /// Brings a pre-identity install forward. It moves to AwaitingTrust and stays
    /// unrunnable — the one thing that must never happen here is silently
    /// computing today's hash and calling that an approval, because that would
    /// grant trust to whatever binary happens to be on disk right now.
    /// </summary>
    public static ConnectorManifest MigrateLegacy(ConnectorManifest manifest)
    {
        ArgumentNullException.ThrowIfNull(manifest);
        if (!string.IsNullOrWhiteSpace(manifest.ExecutableSha256))
            return manifest;
        return manifest with
        {
            TrustState = ConnectorTrustState.AwaitingTrust,
            TrustedAtUtc = null,
            ApprovedManifestFingerprint = null
        };
    }

    /// <summary>
    /// Records an explicit user approval: the executable identity and the manifest
    /// fingerprint are computed and persisted AT THIS MOMENT, which is what later
    /// launches are compared against. Refuses to approve anything outside the
    /// controlled installation location.
    /// </summary>
    public static ConnectorManifest Approve(
        ConnectorManifest manifest,
        ConnectorInstallationPolicy? installation = null,
        DateTimeOffset? atUtc = null)
    {
        ArgumentNullException.ThrowIfNull(manifest);
        var policy = installation ?? ConnectorInstallationPolicy.Default;
        var isLocal = manifest.Transport == ConnectorTransportKind.Stdio;
        if (isLocal)
        {
            if (!policy.Contains(manifest.ExecutablePath))
                throw new InvalidOperationException(
                    "A connector outside the managed installation location cannot be approved.");
            if (!File.Exists(manifest.ExecutablePath))
                throw new FileNotFoundException(
                    "The connector executable does not exist.", manifest.ExecutablePath);
        }

        var approved = manifest with
        {
            ExecutableSha256 = isLocal ? HashExecutable(manifest.ExecutablePath) : "",
            ApprovedEndpointFingerprint = manifest.ComputeEndpointFingerprint(),
            ApprovedCapabilities = manifest.RequestedCapabilities.ToList(),
            TrustState = ConnectorTrustState.Trusted,
            Enabled = true,
            TrustedAtUtc = atUtc ?? DateTimeOffset.UtcNow,
            ApprovedManifestFingerprint = null
        };
        return approved with
        {
            ApprovedManifestFingerprint = approved.ComputeManifestFingerprint()
        };
    }

    public static string HashExecutable(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream));
    }

    private static ConnectorTrustVerdict Refuse(
        ConnectorTrustState state,
        string code,
        string reason) => new(false, state, code, reason);
}
