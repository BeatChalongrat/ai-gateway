using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Native.Browser;

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
    public const string HostName = BrowserNativeHostPolicy.HostName;

    /// <summary>Chrome Web Store id of the published extension — a build
    /// constant by design. Placeholder until the store listing exists; dev
    /// installs add their load-unpacked id via Settings.</summary>
    public const string DefaultExtensionId = "abcdefghijklmnopabcdefghijklmnop";

    private readonly ILogger<BrowserExtensionInstaller> _logger;

    public BrowserExtensionInstaller(ILogger<BrowserExtensionInstaller> logger) => _logger = logger;

    private static string ManifestPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop", "browser", HostName + ".json");

    private const string RegistryKeyPath = BrowserNativeHostPolicy.RegistryKeyPath;

    public bool IsInstalled
    {
        get
        {
            try
            {
                using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RegistryKeyPath);
                return key?.GetValue(null) is string path
                       && BrowserNativeHostPolicy.TryReadManifest(path, out _);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    public string Install(string? devExtensionId)
    {
        var relayExe = Path.GetFullPath(Path.Combine(
            AppContext.BaseDirectory,
            "SranDesktop.BrowserHost.exe"));
        if (!BrowserNativeHostPolicy.IsSafeExecutable(relayExe))
            throw new InvalidOperationException(
                "The native browser host executable is missing or has unsafe provenance.");

        var origins = new List<string>
        {
            BrowserNativeHostPolicy.OriginFor(DefaultExtensionId)
        };
        if (!string.IsNullOrWhiteSpace(devExtensionId))
            origins.Add(BrowserNativeHostPolicy.OriginFor(devExtensionId.Trim()));

        var manifest = JsonSerializer.Serialize(new
        {
            name = HostName,
            description = "SRAN Desktop browser bridge",
            path = relayExe,
            type = "stdio",
            allowed_origins = origins.Distinct(StringComparer.Ordinal).ToArray(),
            sran_executable_sha256 = BrowserNativeHostPolicy.Sha256(relayExe)
        }, new JsonSerializerOptions { WriteIndented = true });

        Directory.CreateDirectory(Path.GetDirectoryName(ManifestPath)!);
        var temporaryManifest = ManifestPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
        File.WriteAllText(temporaryManifest, manifest);
        File.Move(temporaryManifest, ManifestPath, overwrite: true);

        using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RegistryKeyPath);
        key.SetValue(null, ManifestPath);
        _logger.LogInformation("Registered Chrome native-messaging host {Host} → {Manifest}",
            HostName, ManifestPath);

        // The unpacked extension ships next to the app for dev/load-unpacked.
        return Path.Combine(AppContext.BaseDirectory, "browser-extension");
    }

    public void Uninstall()
    {
        try
        {
            Microsoft.Win32.Registry.CurrentUser.DeleteSubKey(
                RegistryKeyPath,
                throwOnMissingSubKey: false);
            if (File.Exists(ManifestPath))
                File.Delete(ManifestPath);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException
                or System.Security.SecurityException)
        {
            _logger.LogWarning(ex, "Could not remove Chrome native-messaging host");
            throw;
        }
    }
}
