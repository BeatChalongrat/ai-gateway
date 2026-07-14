namespace SranDesktop.Application.Interfaces;

/// <summary>Abstraction over the OS "start with the OS" mechanism (Windows: HKCU Run key).</summary>
public interface IStartupManager
{
    bool IsEnabled { get; }
    void Set(bool enabled);
}
