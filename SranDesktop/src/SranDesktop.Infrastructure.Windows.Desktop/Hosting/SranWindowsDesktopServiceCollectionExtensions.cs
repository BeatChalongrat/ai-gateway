using Microsoft.Extensions.DependencyInjection;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native.Uia;

namespace SranDesktop.Infrastructure.Hosting;

/// <summary>
/// The WindowsDesktop platform pack — for hosts that ARE a WPF application.
/// UI Automation tools and the WPF clipboard tools (which resolve
/// <c>System.Windows.Application.Current?.Dispatcher</c> statically and need
/// a live WPF Application — the reason this pack is separate from
/// <see cref="SranWindowsServiceCollectionExtensions"/>). Call after
/// <c>AddSranCore</c> and <c>AddSranWindows</c>.
/// </summary>
public static class SranWindowsDesktopServiceCollectionExtensions
{
    public static IServiceCollection AddSranWindowsDesktop(this IServiceCollection services)
    {
        // UIA element snapshot shared by read_screen_elements → uia_click/uia_set_value.
        services.AddSingleton<UiaSnapshotStore>();
        services.AddSingleton<IPlatformToolProvider, WindowsDesktopToolProvider>();
        return services;
    }
}

internal sealed class WindowsDesktopToolProvider : IPlatformToolProvider
{
    private readonly UiaSnapshotStore _snapshots;

    public WindowsDesktopToolProvider(UiaSnapshotStore snapshots) => _snapshots = snapshots;

    public IEnumerable<IMcpTool> CreateTools(PlatformToolSlot slot)
    {
        switch (slot)
        {
            case PlatformToolSlot.Clipboard:
                yield return new ReadClipboardTool();
                yield return new WriteClipboardTool();
                break;
            case PlatformToolSlot.UiAutomation:
                // Windows UI Automation layer: element-level control, far
                // more accurate than pixel clicking where apps expose UIA.
                yield return new GetComputerStateTool();
                yield return new ReadScreenElementsTool(_snapshots);
                yield return new UiaClickTool(_snapshots);
                yield return new UiaSetValueTool(_snapshots);
                break;
        }
    }
}
