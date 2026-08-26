namespace SranDesktop.Shell.Seams;

/// <summary>
/// The NATIVE surfaces the control vocabulary routes to instead of letting the
/// page name a sensitive value (SHELL_PHASE5_REPORT.md §2.3/§2.4). Each returns
/// a value the USER chose in an OS/host dialog — never one the page supplied.
/// An interface so the host-query dispatcher is unit-testable with a fake.
/// </summary>
public interface IShellNativeDialogs
{
    /// <summary>Native OpenFileDialog for a .skill/.zip package. Returns the
    /// chosen path (host-side only; never handed to the page) or null if
    /// cancelled.</summary>
    Task<string?> PickSkillPackageAsync();

    /// <summary>Native OpenFolderDialog for a folder to grant. Returns the chosen
    /// path or null if cancelled. The caller still runs FolderGrantValidator.</summary>
    Task<string?> PickFolderAsync();

    /// <summary>Native secret prompt for a connector API key. The key is returned
    /// to the host only (to hand straight to DPAPI via the connector service);
    /// it never enters the page or the bridge.</summary>
    Task<string?> PromptApiKeyAsync(string connectorName);

    /// <summary>The entire native add-custom-connector flow: collect the command
    /// line, test-spawn, show spawn consent, install. Returns true if a connector
    /// was added. The page names no process (§2.4).</summary>
    Task<bool> ShowAddCustomConnectorAsync();
}
