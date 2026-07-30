using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Mcp.Connectors;

/// <summary>
/// The production resolver: connector id in, launch descriptor or named refusal
/// out.
///
/// Every resolution re-does the whole check. That is the point — an approval is
/// a statement about a specific binary declaring a specific manifest asking for
/// a specific set of permissions, and all three can change between the decision
/// and the launch. Caching the verdict would turn "the user trusted this" into
/// "the user once trusted something with this name".
///
/// It also never approves anything. Discovery and resolution are read-only; the
/// only way a connector becomes trusted is <see
/// cref="ConnectorTrustAdministration"/>, which requires evidence of a person.
/// </summary>
public sealed class ConnectorManifestResolver : IConnectorManifestResolver
{
    private readonly string _connectorRoot;
    private readonly IConnectorTrustStore _trust;
    private readonly ConnectorInstallationPolicy _installation;
    private readonly ILogger? _logger;

    /// <summary>Extra declarations that live outside the package layout — the
    /// marketplace's HTTP connectors, which have an install record instead of a
    /// directory. They go through the same trust gate; only their identity
    /// differs, because there is no binary to hash.</summary>
    private readonly Func<IReadOnlyList<ConnectorDeclaration>> _externalDeclarations;

    /// <summary>Connectors the user is testing before installing them. Checked
    /// first, and only ever non-empty for the duration of one explicit test
    /// dial — see <see cref="TransientConnectorGrants"/>.</summary>
    private readonly TransientConnectorGrants? _candidates;

    public ConnectorManifestResolver(
        string connectorRoot,
        IConnectorTrustStore trust,
        ConnectorInstallationPolicy? installation = null,
        Func<IReadOnlyList<ConnectorDeclaration>>? externalDeclarations = null,
        TransientConnectorGrants? candidates = null,
        ILogger? logger = null)
    {
        _connectorRoot = connectorRoot;
        _trust = trust;
        _installation = installation ?? new ConnectorInstallationPolicy([connectorRoot]);
        _externalDeclarations = externalDeclarations ?? (() => []);
        _candidates = candidates;
        _logger = logger;
    }

    public IReadOnlyList<ConnectorDeclaration> Discover()
    {
        var found = new List<ConnectorDeclaration>();
        try
        {
            if (Directory.Exists(_connectorRoot))
                foreach (var directory in Directory.GetDirectories(_connectorRoot))
                {
                    var connectorId = Path.GetFileName(directory);
                    // "workspaces" is where the sandbox mounts a server's own
                    // scratch space. It is not an install, and reading it as one
                    // would let a server manufacture a connector by creating a
                    // directory it already has write access to.
                    if (connectorId is "workspaces" or "quarantine")
                        continue;
                    var read = ConnectorManifestReader.Read(
                        Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
                        connectorId,
                        directory);
                    if (read.Declaration is { } declaration)
                        found.Add(declaration);
                    else if (read.Rejection != ConnectorManifestRejection.Missing)
                        _logger?.LogWarning(
                            "Connector {Id} has an unusable manifest ({Rejection}): {Reason}",
                            connectorId, read.Rejection, read.Reason);
                }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger?.LogWarning(ex, "The connector installation root could not be listed");
        }

        found.AddRange(_externalDeclarations());
        return found;
    }

    public ConnectorDeclaration? ReadDeclaration(string connectorId)
    {
        if (_candidates?.TryResolve(connectorId)?.Declaration is { } candidate)
            return candidate;

        var external = _externalDeclarations()
            .FirstOrDefault(item => string.Equals(
                item.ConnectorId, connectorId, StringComparison.Ordinal));
        if (external is not null)
            return external;

        var directory = InstallationDirectory(connectorId);
        if (directory is null)
            return null;
        return ConnectorManifestReader.Read(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            connectorId,
            directory).Declaration;
    }

    public ConnectorResolution Resolve(string connectorId)
    {
        if (string.IsNullOrWhiteSpace(connectorId))
            return ConnectorResolution.Deny(
                "ConnectorNotInstalled", "No connector id was given.",
                ConnectorTrustState.Untrusted);

        if (_candidates?.TryResolve(connectorId) is { } candidate)
            return candidate;

        var external = _externalDeclarations()
            .FirstOrDefault(item => string.Equals(
                item.ConnectorId, connectorId, StringComparison.Ordinal));
        if (external is not null)
            return ResolveDeclaration(external, "");

        var directory = InstallationDirectory(connectorId);
        if (directory is null)
            return ConnectorResolution.Deny(
                "ConnectorNotInstalled",
                $"No connector '{connectorId}' is installed in the managed location.",
                ConnectorTrustState.Untrusted);

        var read = ConnectorManifestReader.Read(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            connectorId,
            directory);
        if (read.Declaration is not { } declaration)
            return ConnectorResolution.Deny(
                MapRejection(read.Rejection),
                read.Reason ?? "The connector manifest is unusable.",
                ConnectorTrustState.Untrusted);

        // The location check is separate from the traversal check the reader
        // already did: that one proved the binary is inside the connector's own
        // package, this one proves the package is somewhere a drive-by download
        // cannot write to.
        if (declaration.Transport == ConnectorTransportKind.Stdio
            && !_installation.Contains(declaration.ExecutablePath))
            return ConnectorResolution.Deny(
                "ConnectorPathNotControlled",
                "The connector executable is outside the managed installation location.",
                ConnectorTrustState.Invalidated,
                declaration);

        string executableHash;
        try
        {
            executableHash = declaration.Transport == ConnectorTransportKind.Stdio
                ? ConnectorTrustPolicy.HashExecutable(declaration.ExecutablePath)
                : "";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return ConnectorResolution.Deny(
                "ConnectorExecutableUnreadable", ex.Message,
                ConnectorTrustState.Invalidated, declaration);
        }

        return ResolveDeclaration(declaration, executableHash);
    }

    private ConnectorResolution ResolveDeclaration(
        ConnectorDeclaration declaration,
        string executableHash)
    {
        var manifestFingerprint = declaration.ComputeFingerprint(executableHash);
        var record = _trust.Get(declaration.ConnectorId);

        if (record is null)
            return ConnectorResolution.Deny(
                "ConnectorUntrusted",
                "The connector has never been approved. It cannot run until a person "
                + "reviews what it is asking for.",
                ConnectorTrustState.Untrusted,
                declaration);

        if (record.State == ConnectorTrustState.Revoked)
            return ConnectorResolution.Deny(
                "ConnectorRevoked",
                record.StateReason is { Length: > 0 } revoked
                    ? revoked
                    : "Trust for this connector was revoked.",
                ConnectorTrustState.Revoked, declaration, record);

        if (record.State == ConnectorTrustState.AwaitingTrust)
            return ConnectorResolution.Deny(
                "ConnectorAwaitingTrust",
                record.StateReason is { Length: > 0 } awaiting
                    ? awaiting
                    : "The connector is waiting to be approved again.",
                ConnectorTrustState.AwaitingTrust, declaration, record);

        if (!record.Enabled)
            return ConnectorResolution.Deny(
                "ConnectorDisabled",
                "The connector is installed but not enabled.",
                record.State, declaration, record);

        if (record.State != ConnectorTrustState.Trusted)
            return ConnectorResolution.Deny(
                "ConnectorUntrusted",
                "The connector has not been approved by the user.",
                record.State, declaration, record);

        // A pre-identity record is the legacy case. Computing today's hash here
        // and calling it approved is exactly the mistake: it would grant the old
        // decision to whatever binary happens to be on disk now.
        if (declaration.Transport == ConnectorTransportKind.Stdio
            && string.IsNullOrWhiteSpace(record.ExecutableSha256))
            return ConnectorResolution.Deny(
                "ConnectorIdentityUnknown",
                "No executable identity was recorded when this connector was installed, so "
                + "the approval cannot be tied to a specific binary. It must be approved "
                + "again.",
                ConnectorTrustState.AwaitingTrust, declaration, record);

        var expansions = declaration.Permissions.ExpansionsOver(record.ApprovedPermissions);
        if (expansions.Count > 0)
            return ConnectorResolution.Deny(
                "ConnectorPermissionExpanded",
                "The connector now asks for permissions that were never approved: "
                + string.Join(", ", expansions) + ".",
                ConnectorTrustState.Invalidated, declaration, record, expansions);

        if (!string.Equals(record.ManifestFingerprint, manifestFingerprint,
                StringComparison.OrdinalIgnoreCase))
            return ConnectorResolution.Deny(
                DescribeManifestChange(declaration, record, executableHash),
                DescribeManifestChangeReason(declaration, record, executableHash),
                ConnectorTrustState.Invalidated, declaration, record);

        var manifest = ToManifest(declaration, record, executableHash, manifestFingerprint);

        // Re-run the launch-time trust policy even though the fields above already
        // agree. It is the one gate the gateway also calls, and having the
        // resolver disagree with it would mean two different answers to "may this
        // run" depending on who asked.
        var verdict = ConnectorTrustPolicy.Evaluate(manifest, _installation);
        if (!verdict.MayLaunch)
            return ConnectorResolution.Deny(
                verdict.RejectionCode ?? "ConnectorUntrusted",
                verdict.Reason ?? "The connector may not be launched.",
                verdict.State, declaration, record);

        return ConnectorResolution.Allow(new ConnectorLaunchDescriptor(
            manifest, declaration, record, ToRiskPolicy(declaration)));
    }

    /// <summary>Names WHICH part of the declaration moved, because "the manifest
    /// changed" tells the user nothing about whether to re-approve.</summary>
    private static string DescribeManifestChange(
        ConnectorDeclaration declaration,
        ConnectorTrustRecord record,
        string executableHash)
    {
        if (declaration.Transport == ConnectorTransportKind.Stdio
            && !string.Equals(executableHash, record.ExecutableSha256,
                StringComparison.OrdinalIgnoreCase))
            return "ConnectorExecutableChanged";
        if (!string.Equals(declaration.ExecutablePath, record.ExecutablePath,
                StringComparison.OrdinalIgnoreCase))
            return "ConnectorExecutablePathChanged";
        if (!string.Equals(declaration.EndpointFingerprint(), record.EndpointFingerprint,
                StringComparison.OrdinalIgnoreCase))
            return "ConnectorEndpointChanged";
        if (!string.Equals(declaration.Version, record.Version, StringComparison.Ordinal))
            return "ConnectorVersionChanged";
        if (!string.Equals(declaration.Publisher ?? "", record.Publisher ?? "",
                StringComparison.Ordinal))
            return "ConnectorPublisherChanged";
        if (!string.Equals(declaration.InstallSource, record.InstallSource,
                StringComparison.Ordinal))
            return "ConnectorInstallSourceChanged";
        return "ConnectorManifestChanged";
    }

    private static string DescribeManifestChangeReason(
        ConnectorDeclaration declaration,
        ConnectorTrustRecord record,
        string executableHash) =>
        DescribeManifestChange(declaration, record, executableHash) switch
        {
            "ConnectorExecutableChanged" =>
                "The executable changed after it was trusted and must be approved again.",
            "ConnectorExecutablePathChanged" =>
                "The connector runs a different file than the one that was approved.",
            "ConnectorEndpointChanged" =>
                "The connector points at a different server than the one that was approved.",
            "ConnectorVersionChanged" =>
                $"Version {record.Version} was approved; {declaration.Version} is installed.",
            "ConnectorPublisherChanged" =>
                "The connector's publisher changed after it was approved.",
            "ConnectorInstallSourceChanged" =>
                "The connector came from a different source than the approved install.",
            _ => "The connector's declaration changed after it was approved."
        };

    /// <summary>
    /// Projects the resolved declaration into the shape the launch-time trust
    /// policy speaks.
    ///
    /// The two fingerprints are deliberately not the same value. The declaration
    /// fingerprint covers the manifest FILE and is what the stored decision is
    /// compared against — that check has already happened by the time we get
    /// here. The manifest fingerprint covers the projection, and exists so
    /// <see cref="ConnectorTrustPolicy.Evaluate"/> can detect tampering between
    /// this point and the launch. Carrying the first into the field the second
    /// is checked against would make every approved connector look modified.
    /// </summary>
    internal static ConnectorManifest ToManifest(
        ConnectorDeclaration declaration,
        ConnectorTrustRecord record,
        string executableHash,
        string manifestFingerprint)
    {
        var projection = BuildManifest(declaration, record, executableHash, manifestFingerprint);
        return projection with
        {
            ApprovedManifestFingerprint = projection.ComputeManifestFingerprint()
        };
    }

    private static ConnectorManifest BuildManifest(
        ConnectorDeclaration declaration,
        ConnectorTrustRecord record,
        string executableHash,
        string manifestFingerprint) => new()
    {
        ConnectorId = declaration.ConnectorId,
        Name = declaration.Name,
        Version = declaration.Version,
        Publisher = declaration.Publisher,
        Transport = declaration.Transport,
        ServerUrl = declaration.ServerUrl,
        ExecutablePath = declaration.ExecutablePath,
        ExecutableSha256 = executableHash,
        InstallSource = declaration.InstallSource,
        ProtocolVersion = declaration.ProtocolVersion,
        ApprovedBy = record.ApprovedBy,
        RequestedCapabilities = declaration.Permissions.Capabilities,
        ApprovedCapabilities = record.ApprovedPermissions.Capabilities,
        WorkspaceAccess = declaration.Permissions.WorkspaceAccess,
        RequiresNetwork = declaration.Permissions.RequiresNetwork,
        CredentialRequirements = declaration.Permissions.CredentialRequirements,
        ApprovedManifestFingerprint = manifestFingerprint,
        ApprovedEndpointFingerprint = record.EndpointFingerprint,
        TrustState = record.State,
        Enabled = record.Enabled,
        TrustedAtUtc = record.ApprovedAtUtc,
        LastVerifiedUtc = DateTimeOffset.UtcNow
    };

    /// <summary>
    /// The risk policy comes from the APPROVED declaration, never from anything
    /// the server says at runtime. A declaration is only "approved" here because
    /// its fingerprint still matches the decision, which is what makes the
    /// classification a user statement rather than a server one.
    /// </summary>
    internal static ConnectorRiskPolicy ToRiskPolicy(ConnectorDeclaration declaration)
    {
        var declared = declaration.ReadOnlyTools.Count
                       + declaration.LocalMutationTools.Count
                       + declaration.ExternalMutationTools.Count
                       + declaration.DestructiveTools.Count > 0;
        return new ConnectorRiskPolicy
        {
            ConnectorId = declaration.ConnectorId,
            PolicyDeclared = declared,
            ReadOnlyTools = declaration.ReadOnlyTools,
            LocalMutationTools = declaration.LocalMutationTools,
            ExternalMutationTools = declaration.ExternalMutationTools,
            DestructiveTools = declaration.DestructiveTools,
            VerificationTools = declaration.VerificationTools
        };
    }

    private string? InstallationDirectory(string connectorId)
    {
        // The id becomes a directory name, so it has to be one: a connector id of
        // "..\\..\\Windows" would otherwise walk out of the root before any of
        // the manifest checks ran.
        if (connectorId.Any(character =>
                character is '/' or '\\' or ':' || Path.GetInvalidFileNameChars().Contains(character))
            || connectorId is "." or "..")
            return null;
        var directory = Path.Combine(_connectorRoot, connectorId);
        return Directory.Exists(directory) ? directory : null;
    }

    private static string MapRejection(ConnectorManifestRejection rejection) => rejection switch
    {
        ConnectorManifestRejection.Missing => "ConnectorNotInstalled",
        ConnectorManifestRejection.TooLarge => "ConnectorManifestTooLarge",
        ConnectorManifestRejection.Malformed => "ConnectorManifestMalformed",
        ConnectorManifestRejection.SchemaInvalid => "ConnectorManifestInvalid",
        ConnectorManifestRejection.IdMismatch => "ConnectorManifestIdMismatch",
        ConnectorManifestRejection.ExecutableOutsideInstallation =>
            "ConnectorPathNotControlled",
        ConnectorManifestRejection.ExecutableMissing => "ConnectorExecutableMissing",
        ConnectorManifestRejection.UnsupportedTransport => "ConnectorTransportUnsupported",
        ConnectorManifestRejection.UnsupportedProtocolVersion => "ConnectorProtocolUnsupported",
        _ => "ConnectorUntrusted"
    };
}
