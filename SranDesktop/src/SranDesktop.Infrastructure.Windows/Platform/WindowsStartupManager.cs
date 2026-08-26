using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Platform;

public sealed class WindowsStartupManager : IStartupManager
{
    public bool IsEnabled => AutoStartManager.IsEnabled;

    public void Set(bool enabled) => AutoStartManager.Set(enabled);
}
