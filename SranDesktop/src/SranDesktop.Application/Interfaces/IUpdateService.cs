namespace SranDesktop.Application.Interfaces;

/// <summary>Auto-update pipeline (R11 A1): manifest check → verified download
/// → silent Inno Setup upgrade.</summary>
public interface IUpdateService
{
    /// <summary>Version of the running app (assembly informational version).</summary>
    string CurrentVersion { get; }

    /// <summary>Null when up to date, unreachable, or the manifest is malformed.</summary>
    Task<UpdateCheckResult?> CheckForUpdateAsync(CancellationToken ct = default);

    /// <summary>Download the installer (skips when a verified copy exists);
    /// throws on SHA-256 mismatch after deleting the file.</summary>
    Task<string> DownloadUpdateAsync(
        UpdateCheckResult update, IProgress<double>? progress = null, CancellationToken ct = default);

    /// <summary>Start the installer silently. The caller shuts the app down.</summary>
    void LaunchInstaller(string installerPath);
}

public sealed record UpdateCheckResult(
    string NewVersion,
    string CurrentVersion,
    string DownloadUrl,
    string Sha256,
    string? ReleaseNotes,
    bool IsMandatory);
