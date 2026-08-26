namespace SranDesktop.Shell.Hosting;

/// <summary>
/// What the session controller may do to the browser surface. Implemented by
/// ShellWindow (which owns the WebView2 and its dispatcher); every method is
/// UI-marshalled by the implementer. Token values cross this boundary in
/// memory only — never a log, never a file.
/// </summary>
public interface ISessionSurface
{
    /// <summary>Write both portal session cookies into the shell's WebView2
    /// jar (HttpOnly, Lax, host-only on the gateway host; Secure iff the
    /// gateway is https).</summary>
    Task WriteSessionCookiesAsync(string accessToken, string refreshToken);

    /// <summary>Delete every cookie in the shell's jar (sign-out).</summary>
    Task ClearSessionCookiesAsync();

    /// <summary>Reload the current page (after a re-auth restored cookies).</summary>
    Task ReloadPageAsync();

    /// <summary>Navigate to the configured web-chat page URL.</summary>
    Task NavigatePageAsync();

    /// <summary>Navigate to about:blank — drops the authenticated DOM on
    /// sign-out so nothing readable stays behind the signed-out view.</summary>
    Task NavigateBlankAsync();
}
