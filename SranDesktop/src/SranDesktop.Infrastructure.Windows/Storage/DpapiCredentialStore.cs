using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Storage;

/// <summary>
/// Stores secrets in %APPDATA%\SRAN Desktop\credentials.dat, encrypted with
/// Windows DPAPI scoped to the current user.
/// </summary>
public sealed class DpapiCredentialStore : ICredentialStore
{
    private readonly string _filePath;
    private readonly object _lock = new();
    private readonly ILogger? _logger;

    public DpapiCredentialStore(ILogger<DpapiCredentialStore>? logger = null)
        : this(DefaultFilePath(), logger)
    {
    }

    public DpapiCredentialStore(string filePath, ILogger<DpapiCredentialStore>? logger = null)
    {
        _filePath = filePath;
        _logger = logger;
        Directory.CreateDirectory(Path.GetDirectoryName(filePath)!);
    }

    public void Save(string key, string value)
    {
        lock (_lock)
        {
            var data = LoadAll();
            data[key] = value;
            WriteAll(data);
        }
    }

    public string? Load(string key)
    {
        lock (_lock)
        {
            var data = LoadAll();
            return data.TryGetValue(key, out var value) ? value : null;
        }
    }

    public void Delete(string key)
    {
        lock (_lock)
        {
            var data = LoadAll();
            if (data.Remove(key))
                WriteAll(data);
        }
    }

    private Dictionary<string, string> LoadAll()
    {
        if (!File.Exists(_filePath))
            return new Dictionary<string, string>();

        try
        {
            var encrypted = File.ReadAllBytes(_filePath);
            var decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
            return JsonSerializer.Deserialize<Dictionary<string, string>>(decrypted)
                   ?? new Dictionary<string, string>();
        }
        catch (Exception ex) when (ex is CryptographicException or JsonException)
        {
            // Written by another user profile or corrupt — preserve the
            // evidence before starting fresh, and say so loudly: the user is
            // about to be signed out and should know why.
            var backup = AtomicFile.TryBackupCorrupt(_filePath);
            _logger?.LogError(ex,
                "Credential store was unreadable; backed up to {Backup} and reset (you will need to sign in again)",
                backup);
            return new Dictionary<string, string>();
        }
    }

    private void WriteAll(Dictionary<string, string> data)
    {
        var json = JsonSerializer.Serialize(data);
        var encrypted = ProtectedData.Protect(
            Encoding.UTF8.GetBytes(json),
            null,
            DataProtectionScope.CurrentUser);
        AtomicFile.WriteBytes(_filePath, encrypted);
    }

    private static string DefaultFilePath()
    {
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        return Path.Combine(appData, "SRAN Desktop", "credentials.dat");
    }
}
