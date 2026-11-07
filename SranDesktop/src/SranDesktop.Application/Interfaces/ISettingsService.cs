using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface ISettingsService
{
    UserSettings Current { get; }

    /// <summary>True when no settings file existed at startup (fresh install).</summary>
    bool IsFirstRun { get; }

    /// <summary>Persists the given settings and makes them Current.</summary>
    void Save(UserSettings settings);

    event EventHandler? SettingsChanged;
}
