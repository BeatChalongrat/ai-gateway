namespace SranDesktop.Application.Interfaces;

/// <summary>Registers the Chrome native-messaging host for the browser bridge
/// (Settings → Browser "Install Chrome extension").</summary>
public interface IBrowserExtensionInstaller
{
    /// <summary>True when the HKCU native-messaging host key exists and points
    /// at an existing manifest file.</summary>
    bool IsInstalled { get; }

    /// <summary>Writes the native-messaging manifest + HKCU registry key.
    /// <paramref name="devExtensionId"/> (optional) is added to
    /// allowed_origins next to the store id — for load-unpacked development.
    /// Returns the on-disk extension folder to load unpacked.</summary>
    string Install(string? devExtensionId);

    /// <summary>Drops a <c>pairing.json</c> file into an unpacked extension
    /// folder so the extension can self-provision the current pairing token
    /// (its service worker reads it via <c>chrome.runtime.getURL</c>). This is
    /// how a shell that has no Settings→Browser pane still hands the token to
    /// the extension — replacing the WPF app's copy-token-and-paste flow. The
    /// token sits in a same-user-readable file: no weaker than the DPAPI
    /// credential store it comes from, which any same-user process can already
    /// decrypt. Best-effort: a missing/unwritable folder is logged, not thrown.
    /// Returns true when the file was (re)written.</summary>
    bool ProvisionPairingToken(string extensionFolder, string pairingToken);
}
