using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Storage;

namespace SranDesktop.Infrastructure.Credentials;

/// <summary>
/// Connector credentials, split across two files on purpose.
///
/// Descriptors — account, scopes, expiry, state — go to a plain JSON index,
/// because they have to be listable and are not secret. The tokens go to the
/// DPAPI store under a per-account key, encrypted to the current user.
///
/// Neither file is the application database. That is the point: a token in
/// SQLite ends up in backups, in a copied .db, and in every diagnostic dump
/// anybody ever takes, and none of those places has any idea it is holding a
/// credential.
/// </summary>
public sealed class DpapiConnectorCredentialStore : IConnectorCredentialStore
{
    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
        Converters = { new JsonStringEnumConverter() }
    };

    private readonly string _indexPath;
    private readonly ICredentialStore _secrets;
    private readonly ILogger? _logger;
    private readonly object _gate = new();
    private Dictionary<string, ConnectorCredentialDescriptor>? _index;

    public DpapiConnectorCredentialStore(
        string indexPath,
        ICredentialStore secrets,
        ILogger? logger = null)
    {
        _indexPath = indexPath;
        _secrets = secrets;
        _logger = logger;
    }

    public static string DefaultIndexPath { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop",
        "connector-accounts.json");

    public IReadOnlyList<ConnectorCredentialDescriptor> List(
        string? connectorId = null, string? tenantId = null)
    {
        lock (_gate)
            return Load().Values
                .Where(item => connectorId is null
                               || string.Equals(item.Account.ConnectorId, connectorId,
                                   StringComparison.Ordinal))
                .Where(item => tenantId is null
                               || string.Equals(item.Account.TenantId, tenantId,
                                   StringComparison.Ordinal))
                .OrderBy(item => item.DisplayName, StringComparer.OrdinalIgnoreCase)
                .ToList();
    }

    public ConnectorCredentialDescriptor? Get(ConnectorAccountRef account)
    {
        lock (_gate)
            return Load().GetValueOrDefault(account.Key);
    }

    public Task<ConnectorCredentialDescriptor> SaveAsync(
        ConnectorCredentialDescriptor descriptor,
        string? accessToken,
        string? refreshToken,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(descriptor);
        var sanitised = Sanitise(descriptor) with
        {
            UpdatedUtc = DateTimeOffset.UtcNow,
            HasRefreshToken = !string.IsNullOrEmpty(refreshToken)
        };

        // The secret goes first. A descriptor claiming a token that was never
        // stored would send the next lease looking for something that is not
        // there; the reverse — a stored token with no descriptor — is merely
        // unreachable, and is cleaned up by the next save.
        WriteSecret(descriptor.Account, accessToken, refreshToken);
        lock (_gate)
        {
            // A copy, not the live dictionary — see JsonConnectorTrustStore.SaveAsync.
            var index = new Dictionary<string, ConnectorCredentialDescriptor>(
                Load(), StringComparer.Ordinal)
            {
                [descriptor.Account.Key] = sanitised
            };
            Persist(index);
        }
        return Task.FromResult(sanitised);
    }

    public Task<ConnectorCredentialDescriptor> UpdateAsync(
        ConnectorCredentialDescriptor descriptor,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(descriptor);
        var sanitised = Sanitise(descriptor) with { UpdatedUtc = DateTimeOffset.UtcNow };
        lock (_gate)
        {
            // A copy, not the live dictionary — see JsonConnectorTrustStore.SaveAsync.
            var index = new Dictionary<string, ConnectorCredentialDescriptor>(
                Load(), StringComparer.Ordinal)
            {
                [descriptor.Account.Key] = sanitised
            };
            Persist(index);
        }
        return Task.FromResult(sanitised);
    }

    public Task RemoveAsync(ConnectorAccountRef account, CancellationToken ct = default)
    {
        ClearSecret(account);
        lock (_gate)
        {
            var index = new Dictionary<string, ConnectorCredentialDescriptor>(
                Load(), StringComparer.Ordinal);
            if (index.Remove(account.Key))
                Persist(index);
        }
        return Task.CompletedTask;
    }

    public async Task RemoveConnectorAsync(string connectorId, CancellationToken ct = default)
    {
        foreach (var descriptor in List(connectorId))
            await RemoveAsync(descriptor.Account, ct);
    }

    public (string? AccessToken, string? RefreshToken) ReadSecret(ConnectorAccountRef account)
    {
        try
        {
            var raw = _secrets.Load(SecretKey(account));
            if (string.IsNullOrEmpty(raw))
                return (null, null);
            var secret = JsonSerializer.Deserialize<StoredSecret>(raw, Options);
            return (secret?.AccessToken, secret?.RefreshToken);
        }
        catch (Exception ex) when (ex is JsonException or IOException
                                       or UnauthorizedAccessException)
        {
            // An unreadable secret is an unusable credential, never a reason to
            // proceed without one.
            _logger?.LogWarning(ex,
                "Connector credential for {Account} could not be read", account.Key);
            return (null, null);
        }
    }

    private void WriteSecret(
        ConnectorAccountRef account, string? accessToken, string? refreshToken)
    {
        if (accessToken is null && refreshToken is null)
        {
            ClearSecret(account);
            return;
        }
        _secrets.Save(
            SecretKey(account),
            JsonSerializer.Serialize(new StoredSecret(accessToken, refreshToken), Options));
    }

    private void ClearSecret(ConnectorAccountRef account)
    {
        try
        {
            _secrets.Delete(SecretKey(account));
        }
        catch (IOException ex)
        {
            _logger?.LogDebug(ex, "No stored secret for {Account}", account.Key);
        }
    }

    private static string SecretKey(ConnectorAccountRef account) =>
        $"connector-account:{account.Key}";

    /// <summary>
    /// Last line of defence for the index file. Descriptors are not supposed to
    /// carry secrets, but a provider error message copied into LastError is
    /// exactly the sort of thing that quietly contains one.
    /// </summary>
    private static ConnectorCredentialDescriptor Sanitise(
        ConnectorCredentialDescriptor descriptor) => descriptor with
    {
        DisplayName = SensitiveDataRedactor.Redact(descriptor.DisplayName),
        LastError = descriptor.LastError is null
            ? null
            : SensitiveDataRedactor.Redact(descriptor.LastError),
        Scopes = descriptor.Scopes.Select(SensitiveDataRedactor.Redact).ToList()
    };

    private Dictionary<string, ConnectorCredentialDescriptor> Load()
    {
        if (_index is not null)
            return _index;
        try
        {
            if (File.Exists(_indexPath))
            {
                var loaded = JsonSerializer.Deserialize<List<ConnectorCredentialDescriptor>>(
                    File.ReadAllText(_indexPath), Options);
                if (loaded is not null)
                    return _index = loaded
                        .Where(item => item.Account is not null)
                        .ToDictionary(item => item.Account.Key, StringComparer.Ordinal);
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException
                                       or UnauthorizedAccessException)
        {
            AtomicFile.TryBackupCorrupt(_indexPath);
            _logger?.LogError(ex,
                "The connector account index is unreadable; every account is treated as "
                + "disconnected until it is reconnected");
        }
        return _index = new Dictionary<string, ConnectorCredentialDescriptor>(
            StringComparer.Ordinal);
    }

    /// <summary>Disk first, cache second — see JsonConnectorTrustStore.Persist.
    /// A cache that gets ahead of the file makes a failed write look like a
    /// connected account until the next restart.</summary>
    private void Persist(Dictionary<string, ConnectorCredentialDescriptor> index)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_indexPath)!);
        AtomicFile.Write(_indexPath, JsonSerializer.Serialize(index.Values.ToList(), Options));
        _index = index;
    }

    private sealed record StoredSecret(string? AccessToken, string? RefreshToken);
}
