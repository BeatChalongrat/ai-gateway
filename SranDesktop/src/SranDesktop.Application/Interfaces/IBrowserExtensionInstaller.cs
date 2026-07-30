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

    /// <summary>Removes the per-user registration and manifest. This does not
    /// delete the extension or browser profile.</summary>
    void Uninstall();
}
