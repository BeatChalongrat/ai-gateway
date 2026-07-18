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

    private static string ManifestPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop", "browser", HostName + ".json");

    private const string RegistryKeyPath =
        @"Software\Google\Chrome\NativeMessagingHosts\" + HostName;

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
        var relayExe = Path.Combine(AppContext.BaseDirectory, "SranDesktop.BrowserHost.exe");

        var origins = new List<string> { $"chrome-extension://{DefaultExtensionId}/" };
        if (!string.IsNullOrWhiteSpace(devExtensionId))
            origins.Add($"chrome-extension://{devExtensionId.Trim()}/");

        var manifest = JsonSerializer.Serialize(new
        {
            name = HostName,
            description = "SRAN Desktop browser bridge",
            path = relayExe,
            type = "stdio",
            allowed_origins = origins
        }, new JsonSerializerOptions { WriteIndented = true });

        Directory.CreateDirectory(Path.GetDirectoryName(ManifestPath)!);
        File.WriteAllText(ManifestPath, manifest);

        using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RegistryKeyPath);
        key.SetValue(null, ManifestPath);
        _logger.LogInformation("Registered Chrome native-messaging host {Host} → {Manifest}",
            HostName, ManifestPath);

        // The unpacked extension ships next to the app for dev/load-unpacked.
        return Path.Combine(AppContext.BaseDirectory, "browser-extension");
    }
}
