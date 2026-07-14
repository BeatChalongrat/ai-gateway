using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Auto-update service (R11 A1). Reads a JSON manifest (configurable URL),
/// compares versions, downloads the Inno Setup installer with progress +
/// SHA-256 verification, and launches it silently.
/// </summary>
public sealed class UpdateService : IUpdateService
{
    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;
    private readonly string _manifestUrl;
    private readonly ILogger<UpdateService> _logger;

    private static readonly string DownloadDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SRAN Desktop", "updates");

    public string CurrentVersion { get; }

    public UpdateService(string? manifestUrl, ILogger<UpdateService> logger, HttpClient? http = null)
    {
        _manifestUrl = string.IsNullOrEmpty(manifestUrl)
            ? "https://releases.sran.ai/desktop/latest.json"
            : manifestUrl!;
        _logger = logger;
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
        // Split('+') drops SourceLink build metadata ("1.0.0+<gitsha>") — the
        // sha is noise everywhere this is displayed (login footer, settings).
        CurrentVersion = (Assembly.GetEntryAssembly()?
                              .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
                              .InformationalVersion
                          ?? Assembly.GetEntryAssembly()?.GetName().Version?.ToString(3)
                          ?? "0.0.0").Split('+')[0];
    }

    public async Task<UpdateCheckResult?> CheckForUpdateAsync(CancellationToken ct = default)
    {
        try
        {
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(20));
            var json = await _http.GetStringAsync(_manifestUrl, cts.Token);
            var manifest = JsonSerializer.Deserialize<UpdateManifest>(json, JsonOptions);
            if (manifest is null || string.IsNullOrEmpty(manifest.Version)
                || string.IsNullOrEmpty(manifest.DownloadUrl))
                return null;

            if (!TryParseVersion(CurrentVersion, out var current)
                || !TryParseVersion(manifest.Version, out var latest)
                || latest <= current)
                return null;

            var mandatory = manifest.Mandatory
                || (TryParseVersion(manifest.MinVersion ?? "", out var min) && current < min);

            return new UpdateCheckResult(
                manifest.Version, CurrentVersion,
                ResolveDownloadUrl(manifest.DownloadUrl),
                manifest.Sha256 ?? "",
                manifest.ReleaseNotes,
                mandatory);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            _logger.LogWarning(ex, "Update check failed");
            return null;
        }
    }

    /// <summary>Manifests may carry a relative downloadUrl (same host as the manifest).</summary>
    internal string ResolveDownloadUrl(string downloadUrl)
    {
        if (Uri.TryCreate(downloadUrl, UriKind.Absolute, out _))
            return downloadUrl;
        var baseUri = new Uri(_manifestUrl);
        return new Uri(baseUri, downloadUrl).ToString();
    }

    /// <summary>"1.2.0-beta+sha" → 1.2.0.</summary>
    internal static bool TryParseVersion(string text, out Version version)
    {
        version = new Version(0, 0);
        var core = text.Split('-', '+')[0].Trim();
        if (core.Length == 0)
            return false;
        if (!core.Contains('.'))
            core += ".0";
        return Version.TryParse(core, out version!);
    }

    public async Task<string> DownloadUpdateAsync(
        UpdateCheckResult update, IProgress<double>? progress = null, CancellationToken ct = default)
    {
        Directory.CreateDirectory(DownloadDir);
        var filePath = Path.Combine(DownloadDir, $"SranDesktop-{update.NewVersion}-Setup.exe");

        // A verified copy from a previous attempt is reusable.
        if (File.Exists(filePath) && VerifyHash(filePath, update.Sha256))
        {
            progress?.Report(1);
            return filePath;
        }

        using var response = await _http.GetAsync(
            update.DownloadUrl, HttpCompletionOption.ResponseHeadersRead, ct);
        response.EnsureSuccessStatusCode();

        var totalBytes = response.Content.Headers.ContentLength ?? -1;
        await using (var contentStream = await response.Content.ReadAsStreamAsync(ct))
        await using (var fileStream = new FileStream(
            filePath, FileMode.Create, FileAccess.Write, FileShare.None, 81920, useAsync: true))
        {
            var buffer = new byte[81920];
            long downloaded = 0;
            int bytesRead;
            while ((bytesRead = await contentStream.ReadAsync(buffer, ct)) > 0)
            {
                await fileStream.WriteAsync(buffer.AsMemory(0, bytesRead), ct);
                downloaded += bytesRead;
                if (totalBytes > 0)
                    progress?.Report((double)downloaded / totalBytes);
            }
        }

        if (!VerifyHash(filePath, update.Sha256))
        {
            File.Delete(filePath);
            throw new InvalidOperationException(
                "Downloaded file hash mismatch — possible corruption or tampering.");
        }

        progress?.Report(1);
        return filePath;
    }

    public void LaunchInstaller(string installerPath)
    {
        // /SILENT = progress-only UI; /CLOSEAPPLICATIONS lets Inno close us.
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
        {
            FileName = installerPath,
            Arguments = "/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS",
            UseShellExecute = true
        });
    }

    internal static bool VerifyHash(string path, string expectedSha256)
    {
        if (string.IsNullOrEmpty(expectedSha256))
            return true; // manifest didn't pin a hash
        using var sha256 = SHA256.Create();
        using var stream = File.OpenRead(path);
        var hash = Convert.ToHexString(sha256.ComputeHash(stream));
        return hash.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase);
    }

    private sealed record UpdateManifest(
        [property: JsonPropertyName("version")] string? Version,
        [property: JsonPropertyName("downloadUrl")] string? DownloadUrl,
        [property: JsonPropertyName("sha256")] string? Sha256,
        [property: JsonPropertyName("releaseNotes")] string? ReleaseNotes,
        [property: JsonPropertyName("minVersion")] string? MinVersion,
        [property: JsonPropertyName("mandatory")] bool Mandatory);
}
