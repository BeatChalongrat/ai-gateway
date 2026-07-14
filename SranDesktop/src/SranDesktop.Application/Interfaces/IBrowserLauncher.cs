namespace SranDesktop.Application.Interfaces;

/// <summary>Opens a URL in the system browser. Injected so the OAuth
/// connect flow is unit-testable without popping real windows.</summary>
public interface IBrowserLauncher
{
    /// <summary>Best-effort launch; false when the shell refused.</summary>
    bool Open(string url);
}
