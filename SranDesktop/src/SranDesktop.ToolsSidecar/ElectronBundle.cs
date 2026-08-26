using Microsoft.Extensions.Logging;

namespace SranDesktop.ToolsSidecar;

/// <summary>
/// FIX1 (collision #3): rewrite the DEPLOYED extension copy into the Electron
/// flavour so it talks to the Electron native-messaging host, not the WPF one.
/// Only ever touches the sidecar's own deployed copy (never a user folder), and
/// is idempotent. Two edits:
///   * background.js  — <c>const HOST = 'com.sran.browser'</c> → the Electron host;
///   * manifest.json  — the display name gains an "(Electron)" suffix so the owner
///     can tell the two loaded extensions apart in chrome://extensions.
/// A plain string replace on known, fixed lines — not a JS/JSON reparse — because
/// the bundle is ours and these two anchors are stable.
/// </summary>
public static class ElectronBundle
{
    private const string DefaultHostLine = "const HOST = 'com.sran.browser';";
    private const string DefaultManifestName = "\"name\": \"SRAN Desktop Browser Bridge\"";
    private const string ElectronManifestName = "\"name\": \"SRAN Desktop Browser Bridge (Electron)\"";

    public static void Rewrite(string deployedFolder, string electronHostName, ILogger logger)
    {
        try
        {
            var bg = Path.Combine(deployedFolder, "background.js");
            if (File.Exists(bg))
            {
                var text = File.ReadAllText(bg);
                var electronLine = $"const HOST = '{electronHostName}';";
                if (text.Contains(DefaultHostLine, StringComparison.Ordinal))
                {
                    File.WriteAllText(bg, text.Replace(DefaultHostLine, electronLine));
                }
                else if (!text.Contains(electronLine, StringComparison.Ordinal))
                {
                    logger.LogWarning("background.js HOST anchor not found; the deployed extension may target the wrong native host");
                }
            }

            var manifest = Path.Combine(deployedFolder, "manifest.json");
            if (File.Exists(manifest))
            {
                var text = File.ReadAllText(manifest);
                if (text.Contains(DefaultManifestName, StringComparison.Ordinal))
                    File.WriteAllText(manifest, text.Replace(DefaultManifestName, ElectronManifestName));
            }
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Could not rewrite the deployed extension to the Electron flavour");
        }
    }
}
