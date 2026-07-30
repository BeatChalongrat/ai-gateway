using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Mcp.Connectors;

public enum ConnectorInstallOutcome
{
    Installed,
    Updated,
    RolledBack,
    Uninstalled,
    Rejected
}

public sealed record ConnectorInstallResult(
    ConnectorInstallOutcome Outcome,
    string? FailureCode,
    string? FailureReason,
    ConnectorDeclaration? Declaration,

    /// <summary>Permissions the new version asks for that the current approval
    /// does not cover. Non-empty means the update installed but cannot run until
    /// somebody approves it again.</summary>
    IReadOnlyList<string> PermissionExpansions,

    /// <summary>True when the install landed but a person still has to approve
    /// it. Separated from failure because the package IS on disk.</summary>
    bool RequiresApproval)
{
    public bool Succeeded => Outcome != ConnectorInstallOutcome.Rejected;

    public static ConnectorInstallResult Reject(string code, string reason) =>
        new(ConnectorInstallOutcome.Rejected, code, reason, null, [], false);
}

/// <summary>
/// Moves connector packages in and out of the managed location.
///
/// Everything here is built around one shape: stage somewhere else, validate the
/// staged copy completely, then switch atomically. A connector that is half
/// updated is a connector whose manifest describes one binary while a different
/// one sits next to it — and that is exactly the state the trust check is
/// supposed to be able to rely on.
///
/// Installing never grants trust. A freshly installed or updated package is
/// unapproved, and an update that widens permissions loses the approval the old
/// version had.
/// </summary>
public sealed class ConnectorPackageInstaller
{
    private const string PreviousSuffix = ".previous";
    private const string StagingSuffix = ".staging";

    private readonly string _connectorRoot;
    private readonly IConnectorTrustStore _trust;
    private readonly IConnectorCredentialStore? _credentials;
    private readonly Func<string, Task>? _stopSessions;
    private readonly ILogger? _logger;

    public ConnectorPackageInstaller(
        string connectorRoot,
        IConnectorTrustStore trust,
        IConnectorCredentialStore? credentials = null,
        Func<string, Task>? stopSessions = null,
        ILogger? logger = null)
    {
        _connectorRoot = connectorRoot;
        _trust = trust;
        _credentials = credentials;
        _stopSessions = stopSessions;
        _logger = logger;
    }

    /// <summary>Where a package is assembled before it is allowed to be a
    /// connector. Outside the discovery path, so a half-copied package is never
    /// resolvable.</summary>
    public string StagingRoot => Path.Combine(_connectorRoot, ".staging");

    /// <summary>
    /// Installs or updates a connector from a prepared source directory.
    ///
    /// The source is validated as a package BEFORE anything is moved: an invalid
    /// manifest, a traversing executable path, or an id that does not match the
    /// directory are all refusals that leave the existing install untouched.
    /// </summary>
    public async Task<ConnectorInstallResult> InstallAsync(
        string connectorId,
        string sourceDirectory,
        CancellationToken ct = default)
    {
        if (!IsSafeConnectorId(connectorId))
            return ConnectorInstallResult.Reject(
                "ConnectorIdInvalid",
                "A connector id must be a plain directory name.");
        if (!Directory.Exists(sourceDirectory))
            return ConnectorInstallResult.Reject(
                "PackageMissing", $"No package at {sourceDirectory}.");

        var target = Path.Combine(_connectorRoot, connectorId);
        var staging = target + StagingSuffix;
        var previous = target + PreviousSuffix;

        try
        {
            // 1. Stage. The copy is validated in place so a malformed package
            //    never occupies the name a running connector answers to.
            SafeDelete(staging);
            CopyTree(sourceDirectory, staging);

            var read = ConnectorManifestReader.Read(
                Path.Combine(staging, ConnectorManifestReader.ManifestFileName),
                connectorId,
                staging);
            if (read.Declaration is not { } declaration)
            {
                SafeDelete(staging);
                return ConnectorInstallResult.Reject(
                    read.Rejection.ToString(),
                    read.Reason ?? "The package manifest is unusable.");
            }

            var existing = _trust.Get(connectorId);
            var isUpdate = Directory.Exists(target);

            // 2. What is it asking for that the current decision does not cover?
            //    Computed BEFORE the switch so the answer describes the version
            //    about to run, not the one that was there.
            var expansions = existing is not null
                ? declaration.Permissions.ExpansionsOver(existing.ApprovedPermissions)
                : [];

            // 3. A running server has to stop before its files move underneath
            //    it, or the update leaves a live process bound to a deleted image.
            if (isUpdate && _stopSessions is not null)
                await _stopSessions(connectorId);

            // 4. Atomic switch. The previous version is kept, which is the whole
            //    of what makes rollback possible.
            SafeDelete(previous);
            if (isUpdate)
                Directory.Move(target, previous);
            Directory.Move(staging, target);

            // 5. The approval does not survive the switch. A new binary is a new
            //    thing, whatever its version number says.
            var reason = isUpdate
                ? $"Updated to version {declaration.Version}. Approve it to run it."
                : "Newly installed. Approve it to run it.";
            await _trust.SaveAsync(new ConnectorTrustRecord
            {
                ConnectorId = connectorId,
                Version = declaration.Version,
                Transport = declaration.Transport,
                ExecutablePath = declaration.ExecutablePath,
                Publisher = declaration.Publisher,
                InstallSource = declaration.InstallSource,
                ApprovedPermissions = ConnectorPermissionSet.Empty,
                Enabled = false,
                State = ConnectorTrustState.AwaitingTrust,
                StateReason = expansions.Count > 0
                    ? reason + " It now asks for: " + string.Join(", ", expansions) + "."
                    : reason
            }, ct);

            _logger?.LogInformation(
                "Connector {Id} {Action} to {Version}; awaiting approval",
                connectorId, isUpdate ? "updated" : "installed", declaration.Version);
            return new ConnectorInstallResult(
                isUpdate ? ConnectorInstallOutcome.Updated : ConnectorInstallOutcome.Installed,
                null, null, declaration, expansions, RequiresApproval: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // A failure part-way leaves the previous directory in place; recover
            // it so the connector that WAS working still is.
            SafeDelete(staging);
            TryRestorePrevious(target, previous);
            _logger?.LogError(ex, "Connector {Id} install failed", connectorId);
            return ConnectorInstallResult.Reject("InstallFailed", ex.Message);
        }
    }

    /// <summary>
    /// Puts the previous version back.
    ///
    /// The restored package still has to be approved. It was trusted once, but
    /// the trust record was replaced by the update, and reinstating an approval
    /// from memory would be trusting a hash nobody re-checked.
    /// </summary>
    public async Task<ConnectorInstallResult> RollbackAsync(
        string connectorId, CancellationToken ct = default)
    {
        var target = Path.Combine(_connectorRoot, connectorId);
        var previous = target + PreviousSuffix;
        if (!Directory.Exists(previous))
            return ConnectorInstallResult.Reject(
                "NoPreviousVersion",
                "There is no earlier version of this connector to go back to.");

        try
        {
            if (_stopSessions is not null)
                await _stopSessions(connectorId);

            var failed = target + ".rolled-back";
            SafeDelete(failed);
            if (Directory.Exists(target))
                Directory.Move(target, failed);
            Directory.Move(previous, target);
            SafeDelete(failed);

            var read = ConnectorManifestReader.Read(
                Path.Combine(target, ConnectorManifestReader.ManifestFileName),
                connectorId,
                target);

            await _trust.SaveAsync(new ConnectorTrustRecord
            {
                ConnectorId = connectorId,
                Version = read.Declaration?.Version ?? "unknown",
                Transport = read.Declaration?.Transport ?? ConnectorTransportKind.Stdio,
                ExecutablePath = read.Declaration?.ExecutablePath ?? "",
                Publisher = read.Declaration?.Publisher,
                InstallSource = read.Declaration?.InstallSource ?? "",
                ApprovedPermissions = ConnectorPermissionSet.Empty,
                Enabled = false,
                State = ConnectorTrustState.AwaitingTrust,
                StateReason =
                    "Rolled back to the previous version. Approve it to run it again."
            }, ct);

            _logger?.LogInformation("Connector {Id} rolled back", connectorId);
            return new ConnectorInstallResult(
                ConnectorInstallOutcome.RolledBack, null, null,
                read.Declaration, [], RequiresApproval: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger?.LogError(ex, "Connector {Id} rollback failed", connectorId);
            return ConnectorInstallResult.Reject("RollbackFailed", ex.Message);
        }
    }

    /// <summary>
    /// Removes the connector.
    ///
    /// Credentials are handled by an explicit choice rather than a default:
    /// keeping them means a reinstall silently regains access to somebody's
    /// mailbox, and dropping them means an accidental uninstall costs a
    /// reconnect. The caller has to say which it meant.
    /// </summary>
    public async Task<ConnectorInstallResult> UninstallAsync(
        string connectorId,
        bool removeCredentials,
        CancellationToken ct = default)
    {
        var target = Path.Combine(_connectorRoot, connectorId);
        try
        {
            if (_stopSessions is not null)
                await _stopSessions(connectorId);

            SafeDelete(target);
            SafeDelete(target + PreviousSuffix);
            SafeDelete(target + StagingSuffix);
            SafeDelete(Path.Combine(_connectorRoot, "workspaces", connectorId));

            if (removeCredentials && _credentials is not null)
                await _credentials.RemoveConnectorAsync(connectorId, ct);

            // The trust record becomes the audit trail: revoked, with the reason
            // and the identity that was trusted still readable. Deleting it would
            // let a later reinstall of the same id look like it had never been
            // seen before.
            if (_trust.Get(connectorId) is { } existing)
                await _trust.SaveAsync(existing with
                {
                    State = ConnectorTrustState.Revoked,
                    Enabled = false,
                    RevokedAtUtc = DateTimeOffset.UtcNow,
                    StateReason = removeCredentials
                        ? "Uninstalled; connected accounts were removed."
                        : "Uninstalled; connected accounts were kept."
                }, ct);

            _logger?.LogInformation(
                "Connector {Id} uninstalled (credentials removed: {Removed})",
                connectorId, removeCredentials);
            return new ConnectorInstallResult(
                ConnectorInstallOutcome.Uninstalled, null, null, null, [], false);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return ConnectorInstallResult.Reject("UninstallFailed", ex.Message);
        }
    }

    /// <summary>Whether a package can be put back. Surfaced so the UI can offer
    /// rollback only when there is something to roll back to.</summary>
    public bool HasPreviousVersion(string connectorId) =>
        Directory.Exists(Path.Combine(_connectorRoot, connectorId) + PreviousSuffix);

    /// <summary>Clears staging left by a process that died mid-install. Called at
    /// startup: a leftover .staging directory is not an install, but it is
    /// occupying disk and looks alarming in a folder listing.</summary>
    public void CleanStaging()
    {
        try
        {
            if (!Directory.Exists(_connectorRoot))
                return;
            foreach (var directory in Directory.GetDirectories(_connectorRoot)
                         .Where(path => path.EndsWith(StagingSuffix, StringComparison.Ordinal)
                                        || path.EndsWith(".rolled-back", StringComparison.Ordinal)))
                SafeDelete(directory);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger?.LogDebug(ex, "Staging cleanup skipped");
        }
    }

    private void TryRestorePrevious(string target, string previous)
    {
        try
        {
            if (!Directory.Exists(target) && Directory.Exists(previous))
                Directory.Move(previous, target);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger?.LogError(ex,
                "The previous connector version could not be restored after a failed install");
        }
    }

    internal static bool IsSafeConnectorId(string connectorId) =>
        !string.IsNullOrWhiteSpace(connectorId)
        && connectorId is not "." and not ".."
        && !connectorId.Any(character =>
            character is '/' or '\\' or ':'
            || Path.GetInvalidFileNameChars().Contains(character));

    /// <summary>
    /// Copies a package, refusing anything that would land outside the
    /// destination. A zip or a source tree can contain a traversing entry, and
    /// "copy everything" is where that becomes a write to System32.
    /// </summary>
    private static void CopyTree(string source, string destination)
    {
        var root = Path.GetFullPath(destination)
                       .TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file);
            var target = Path.GetFullPath(Path.Combine(destination, relative));
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new UnauthorizedAccessException(
                    $"The package entry '{relative}' resolves outside the install directory.");
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    private static void SafeDelete(string directory)
    {
        if (!Directory.Exists(directory))
            return;
        Directory.Delete(directory, recursive: true);
    }
}
