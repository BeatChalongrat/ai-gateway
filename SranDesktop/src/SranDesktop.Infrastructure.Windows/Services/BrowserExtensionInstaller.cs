using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Native-messaging host registration (browser tools). Chrome finds native
/// hosts via HKCU\Software\Google\Chrome\NativeMessagingHosts\{name} → a
/// manifest JSON whose "path" points at the relay exe. Per-user, no
/// elevation — same style as the sran:// scheme registration in App.xaml.cs.
/// Re-running is safe: manifest + key are simply rewritten (that's also how a
/// dev extension id gets added).
/// </summary>
public sealed class BrowserExtensionInstaller : IBrowserExtensionInstaller
{
    public const string HostName = "com.sran.browser";

    /// <summary>Chrome Web Store id of the published extension — a build
    /// constant by design. Placeholder until the store listing exists; dev
    /// installs add their load-unpacked id via Settings.</summary>
    public const string DefaultExtensionId = "sran0000000000000000000000000000";

    private readonly ILogger<BrowserExtensionInstaller> _logger;

    public BrowserExtensionInstaller(ILogger<BrowserExtensionInstaller> logger) => _logger = logger;

    /// <summary>E4: a host that keeps its state elsewhere (the Electron sidecar)
    /// names its own manifest file and relay exe. FIX1 (collision #2): it also
    /// names its own native-messaging HOST — a distinct registry key and manifest
    /// <c>name</c> — so registering the Electron host no longer overwrites the WPF
    /// app's key (last-writer-wins was why one app's relay was never spawned). The
    /// WPF app keeps the defaults below (<paramref name="hostName"/> null ⇒
    /// <see cref="HostName"/>) — behaviour unchanged for it.</summary>
    public BrowserExtensionInstaller(ILogger<BrowserExtensionInstaller> logger, string manifestPath, string relayExePath, string? hostName = null)
    {
        _logger = logger;
        _manifestPath = manifestPath;
        _relayExePath = relayExePath;
        _hostName = hostName;
    }

    private readonly string? _manifestPath;
    private readonly string? _relayExePath;
    private readonly string? _hostName;

    /// <summary>The native-messaging host name this instance registers. Default =
    /// the shared <see cref="HostName"/> const (WPF); the Electron sidecar passes
    /// <c>com.sran.browser.electron</c>.</summary>
    private string EffectiveHostName => string.IsNullOrEmpty(_hostName) ? HostName : _hostName;

    private string ManifestPath => _manifestPath ?? Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop", "browser", HostName + ".json");

    /// <summary>Chrome derives an unpacked extension's id from its absolute
    /// folder path: SHA-256 of the path, first 32 hex digits mapped 0-9a-f →
    /// a-p. Computing it lets the manifest's allowed_origins name the bundle
    /// the host deployed, wherever it deployed it.</summary>
    public static string UnpackedExtensionIdFor(string folder)
    {
        var full = Path.TrimEndingDirectorySeparator(Path.GetFullPath(folder));
        var hash = System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(full));
        var hex = Convert.ToHexString(hash).ToLowerInvariant()[..32];
        var sb = new System.Text.StringBuilder(32);
        foreach (var c in hex)
            sb.Append((char)('a' + Convert.ToInt32(c.ToString(), 16)));
        return sb.ToString();
    }

    private string RegistryKeyPath =>
        @"Software\Google\Chrome\NativeMessagingHosts\" + EffectiveHostName;

    public bool IsInstalled
    {
        get
        {
            try
            {
                using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RegistryKeyPath);
                return key?.GetValue(null) is string path && File.Exists(path);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    public string Install(string? devExtensionId)
    {
        var relayExe = _relayExePath ?? Path.Combine(AppContext.BaseDirectory, "SranDesktop.BrowserHost.exe");

        var origins = new List<string> { $"chrome-extension://{DefaultExtensionId}/" };
        if (!string.IsNullOrWhiteSpace(devExtensionId))
            origins.Add($"chrome-extension://{devExtensionId.Trim()}/");

        var manifest = JsonSerializer.Serialize(new
        {
            name = EffectiveHostName,
            description = "SRAN Desktop browser bridge",
            path = relayExe,
            type = "stdio",
            allowed_origins = origins
        }, new JsonSerializerOptions { WriteIndented = true });

        Directory.CreateDirectory(Path.GetDirectoryName(ManifestPath)!);
        File.WriteAllText(ManifestPath, manifest);

        using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RegistryKeyPath);
        key.SetValue(null, ManifestPath);
        _logger.LogInformation("Registered Chrome native-messaging host {Host} → {Manifest} (allowed_origins: {Count} id(s))",
            EffectiveHostName, ManifestPath, origins.Count);

        // The unpacked extension ships next to the app for dev/load-unpacked.
        return Path.Combine(AppContext.BaseDirectory, "browser-extension");
    }

    /// <summary>Name of the self-provisioning token file (read by background.js
    /// via chrome.runtime.getURL). Kept out of git — see .gitignore.</summary>
    public const string PairingFileName = "pairing.json";

    public bool ProvisionPairingToken(string extensionFolder, string pairingToken)
    {
        if (string.IsNullOrWhiteSpace(extensionFolder) || string.IsNullOrEmpty(pairingToken))
            return false;
        try
        {
            if (!Directory.Exists(extensionFolder))
            {
                _logger.LogInformation(
                    "Browser extension folder {Folder} not present — skipping token provisioning (load the extension there, or set BrowserExtensionPath)",
                    extensionFolder);
                return false;
            }

            var target = Path.Combine(extensionFolder, PairingFileName);
            var json = JsonSerializer.Serialize(new { token = pairingToken },
                new JsonSerializerOptions { WriteIndented = true });

            // Skip a rewrite when the token is unchanged: the extension watches
            // its own bundle only loosely, and a no-op write is churn.
            if (File.Exists(target))
            {
                try
                {
                    using var doc = JsonDocument.Parse(File.ReadAllText(target));
                    if (doc.RootElement.TryGetProperty("token", out var t)
                        && string.Equals(t.GetString(), pairingToken, StringComparison.Ordinal))
                        return false;
                }
                catch (Exception) { /* unreadable — overwrite below */ }
            }

            File.WriteAllText(target, json);
            _logger.LogInformation("Provisioned browser pairing token into {Folder}", extensionFolder);
            return true;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not provision browser pairing token into {Folder}", extensionFolder);
            return false;
        }
    }
}
