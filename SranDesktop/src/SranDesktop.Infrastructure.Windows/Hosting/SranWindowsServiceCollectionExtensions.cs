using Microsoft.Extensions.DependencyInjection;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Platform;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Speech;
using SranDesktop.Infrastructure.Storage;

namespace SranDesktop.Infrastructure.Hosting;

/// <summary>
/// The Windows (no-WPF) platform pack. Registers exactly what
/// <c>AddSranCore</c> used to register from this cluster, with the same
/// lifetimes, and the tool provider that contributes the computer-use tools
/// at their old position in the registry. Call after <c>AddSranCore</c>.
/// </summary>
public static class SranWindowsServiceCollectionExtensions
{
    public static IServiceCollection AddSranWindows(this IServiceCollection services)
    {
        // Platform integration
        services.AddSingleton<IStartupManager, WindowsStartupManager>();
        // Storage (DPAPI)
        services.AddSingleton<ICredentialStore, DpapiCredentialStore>();
        // Chrome native-messaging manifest lives in the registry on Windows.
        services.AddSingleton<IBrowserExtensionInstaller, BrowserExtensionInstaller>();
        // Computer-use screenshot→screen coordinate mapping (accuracy
        // overhaul): screenshot writes it, click/scroll/drag/zoom read it.
        services.AddSingleton<ScreenContext>();
        // Consent-flow foreground restore (self-interference fix).
        services.AddSingleton<IForegroundTargetTracker, ForegroundTargetTracker>();
        // R10 Fix 2: composite speech — Azure (Thai) when a key is stored, Windows otherwise.
        services.AddSingleton<ISpeechService, CompositeSpeechService>();
        services.AddSingleton<IPlatformToolProvider, WindowsToolProvider>();
        return services;
    }
}

/// <summary>Computer use (Claude-style: screenshot → look → act → verify;
/// accuracy overhaul: coordinates auto-map through ScreenContext). Same
/// tools, same constructor arguments, same order as before the split.</summary>
internal sealed class WindowsToolProvider : IPlatformToolProvider
{
    private readonly ScreenContext _screen;
    private readonly IForegroundTargetTracker _foreground;

    public WindowsToolProvider(ScreenContext screen, IForegroundTargetTracker foreground)
    {
        _screen = screen;
        _foreground = foreground;
    }

    public IEnumerable<IMcpTool> CreateTools(PlatformToolSlot slot)
    {
        if (slot != PlatformToolSlot.ComputerUse)
            yield break;
        yield return new ComputerScreenshotTool(_screen, _foreground);
        yield return new ComputerClickTool(_screen, _foreground);
        yield return new ComputerTypeTool();
        yield return new ComputerKeyTool();
        yield return new ComputerScrollTool(_screen);
        yield return new ComputerDragTool(_screen);
        yield return new ComputerZoomTool(_screen);
        yield return new OpenApplicationTool();
    }
}
