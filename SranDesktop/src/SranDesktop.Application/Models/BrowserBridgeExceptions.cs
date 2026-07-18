namespace SranDesktop.Application.Models;

/// <summary>No paired Chrome extension connection — browser tools turn this
/// into the "install/enable it (Settings → Browser)" guidance.</summary>
public sealed class BrowserNotConnectedException : Exception
{
    public BrowserNotConnectedException()
        : base("Chrome extension not connected.")
    {
    }
}

/// <summary>The extension answered a request with an error (stale refs, tab
/// gone, blocked page…). The message is model-facing and passed through.</summary>
public sealed class BrowserBridgeException : Exception
{
    public BrowserBridgeException(string message) : base(message)
    {
    }
}
