using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Storage;

namespace SranDesktop.Infrastructure.Mcp.Connectors;

/// <summary>
/// Connector approvals on disk.
///
/// Two properties matter more than the format. First, the file lives OUTSIDE the
/// connector installation root: a connector that could rewrite the record
/// authorising it would need exactly one launch to authorise itself, and the
/// sandbox already gives its server a workspace under that root. Second, writes
/// go through <see cref="AtomicFile"/>, because a half-written trust file after
/// a power cut is a set of approvals nobody made.
///
/// Nothing secret is stored here — hashes and fingerprints only — and the
/// redaction pass exists to keep it that way if a future field is added
/// carelessly.
/// </summary>
public sealed class JsonConnectorTrustStore : IConnectorTrustStore
{
    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
        Converters = { new JsonStringEnumConverter() }
    };

    private readonly string _path;
    private readonly ILogger? _logger;
    private readonly object _gate = new();
    private Dictionary<string, ConnectorTrustRecord>? _records;

    public event EventHandler<ConnectorTrustChange>? TrustChanged;

    public JsonConnectorTrustStore(string path, ILogger? logger = null)
    {
        _path = path;
        _logger = logger;
    }

    /// <summary>The default location: beside the app's other user state, never
    /// under the connector root the servers can see.</summary>
    public static string DefaultPath { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop",
        "connector-trust.json");

    public ConnectorTrustRecord? Get(string connectorId)
    {
        lock (_gate)
            return Load().GetValueOrDefault(connectorId);
    }

    public IReadOnlyList<ConnectorTrustRecord> All()
    {
        lock (_gate)
            return Load().Values.ToList();
    }

    public Task<ConnectorTrustRecord> SaveAsync(
        ConnectorTrustRecord record,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(record);
        var sanitised = Sanitise(record) with { LastValidatedUtc = DateTimeOffset.UtcNow };
        lock (_gate)
        {
            // A COPY, not the live dictionary. Mutating the cache in place would
            // publish the change before the write, so a failed write would leave
            // the process believing in an approval that is not on disk — the
            // whole point of Persist writing first.
            var records = new Dictionary<string, ConnectorTrustRecord>(
                Load(), StringComparer.Ordinal)
            {
                [record.ConnectorId] = sanitised
            };
            Persist(records);
        }
        _logger?.LogInformation(
            "Connector {Id} trust is now {State} (enabled={Enabled}, by={By})",
            record.ConnectorId, sanitised.State, sanitised.Enabled, sanitised.ApprovedBy);
        TrustChanged?.Invoke(this, new ConnectorTrustChange(
            record.ConnectorId, sanitised.State, sanitised.StateReason));
        return Task.FromResult(sanitised);
    }

    public Task RemoveAsync(string connectorId, CancellationToken ct = default)
    {
        lock (_gate)
        {
            var records = new Dictionary<string, ConnectorTrustRecord>(
                Load(), StringComparer.Ordinal);
            if (!records.Remove(connectorId))
                return Task.CompletedTask;
            Persist(records);
        }
        TrustChanged?.Invoke(this, new ConnectorTrustChange(
            connectorId, ConnectorTrustState.Untrusted, "The connector was uninstalled."));
        return Task.CompletedTask;
    }

    /// <summary>
    /// Strips anything that could carry a secret. Hashes, fingerprints, and
    /// permission names are all that a trust decision needs; a credential
    /// REQUIREMENT is a name like "github-token", never the token.
    /// </summary>
    private static ConnectorTrustRecord Sanitise(ConnectorTrustRecord record) => record with
    {
        ApprovedBy = SensitiveDataRedactor.Redact(record.ApprovedBy),
        StateReason = SensitiveDataRedactor.Redact(record.StateReason),
        ApprovedPermissions = record.ApprovedPermissions with
        {
            CredentialRequirements = record.ApprovedPermissions.CredentialRequirements
                .Select(SensitiveDataRedactor.Redact)
                .ToList()
        }
    };

    private Dictionary<string, ConnectorTrustRecord> Load()
    {
        if (_records is not null)
            return _records;
        try
        {
            if (File.Exists(_path))
            {
                var loaded = JsonSerializer.Deserialize<List<ConnectorTrustRecord>>(
                    File.ReadAllText(_path), Options);
                if (loaded is not null)
                    return _records = loaded
                        .Where(record => !string.IsNullOrWhiteSpace(record.ConnectorId))
                        .ToDictionary(record => record.ConnectorId, StringComparer.Ordinal);
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException
                                       or UnauthorizedAccessException)
        {
            // An unreadable trust file must not silently become "no restrictions":
            // an empty map means every connector is unapproved, which is the safe
            // reading. The original is kept so the user can see what was lost.
            AtomicFile.TryBackupCorrupt(_path);
            _logger?.LogError(ex,
                "The connector trust store is unreadable; every connector is treated "
                + "as unapproved until it is approved again");
        }
        return _records = new Dictionary<string, ConnectorTrustRecord>(StringComparer.Ordinal);
    }

    /// <summary>
    /// Writes to disk FIRST, and only then lets the in-memory view advance.
    ///
    /// The other order is the defect this method exists to not have. Updating
    /// the cache first means that when the write fails — a locked file, a full
    /// disk, a redirected profile the user cannot reach — the process still
    /// believes the connector is approved. The card turns green, the resolver
    /// answers Trusted, and nothing on disk agrees; the approval then evaporates
    /// at the next restart with no sign it was ever lost. An approval that is
    /// not durable is not an approval, so the cache must never get ahead of the
    /// file.
    /// </summary>
    private void Persist(Dictionary<string, ConnectorTrustRecord> records)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
        AtomicFile.Write(_path, JsonSerializer.Serialize(records.Values.ToList(), Options));
        _records = records;
    }
}
