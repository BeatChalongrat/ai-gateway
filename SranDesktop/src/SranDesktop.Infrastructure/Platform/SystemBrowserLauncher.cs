using System.Diagnostics;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Platform;

/// <summary>Shell-executes a URL in the user's default browser (Phase 7 C3).</summary>
public sealed class SystemBrowserLauncher : IBrowserLauncher
{
    private readonly ILogger<SystemBrowserLauncher> _logger;

    public SystemBrowserLauncher(ILogger<SystemBrowserLauncher> logger)
    {
        _logger = logger;
    }

    public bool Open(string url)
    {
        // Only ever open http(s) — this launcher exists for the OAuth
        // start URL; refuse anything that could execute locally.
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed)
            || parsed.Scheme is not ("http" or "https"))
        {
            _logger.LogWarning("Refusing to open non-http URL in browser");
            return false;
        }
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            return true;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "System browser launch failed");
            return false;
        }
    }
}
