using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Storage;

public sealed class JsonSettingsService : ISettingsService
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    private readonly string _filePath;
    private readonly object _lock = new();
    private readonly ILogger? _logger;

    public UserSettings Current { get; private set; }

    public bool IsFirstRun { get; }

    public event EventHandler? SettingsChanged;

    public JsonSettingsService(UserSettings defaults, ILogger<JsonSettingsService>? logger = null)
        : this(defaults, DefaultFilePath(), logger)
    {
    }

    public JsonSettingsService(UserSettings defaults, string filePath, ILogger<JsonSettingsService>? logger = null)
    {
        _filePath = filePath;
        _logger = logger;
        Directory.CreateDirectory(Path.GetDirectoryName(filePath)!);
        IsFirstRun = !File.Exists(filePath);
        Current = LoadOrDefault(defaults);
    }

    public void Save(UserSettings settings)
    {
        lock (_lock)
        {
            Current = settings.Clone();
            AtomicFile.Write(_filePath, JsonSerializer.Serialize(Current, JsonOptions));
        }
        SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private UserSettings LoadOrDefault(UserSettings defaults)
    {
        try
        {
            if (File.Exists(_filePath))
            {
                var loaded = JsonSerializer.Deserialize<UserSettings>(File.ReadAllText(_filePath));
                if (loaded is not null)
                    return loaded;
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            // Corrupt settings — preserve the evidence, then start from
            // defaults. Never silently discard the user's file.
            var backup = AtomicFile.TryBackupCorrupt(_filePath);
            _logger?.LogError(ex,
                "Settings file was unreadable; backed up to {Backup} and reset to defaults", backup);
        }
        return defaults.Clone();
    }

    private static string DefaultFilePath()
    {
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        return Path.Combine(appData, "SRAN Desktop", "settings.json");
    }
}
