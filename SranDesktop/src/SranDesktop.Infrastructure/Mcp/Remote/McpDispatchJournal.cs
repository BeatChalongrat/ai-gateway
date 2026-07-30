using System.Text.Json;
using System.Text.Json.Serialization;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Storage;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// File-backed dispatch journal.
///
/// It is written with <see cref="AtomicFile"/> and flushed to disk BEFORE the
/// request is dispatched, not after, because the whole value of the record is in
/// the window where the process dies mid-call. A journal that were written
/// afterwards would be empty in exactly the case it exists for.
///
/// Kept deliberately small and separate from the durable task store: the task
/// store is transactional and rich, but an intent has to be durable at a moment
/// when we are about to hand control to another process, and a single append is
/// the cheapest thing that can be made to survive that.
/// </summary>
public sealed class McpDispatchJournal : IMcpDispatchJournal
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Converters = { new JsonStringEnumConverter() }
    };

    private readonly string _path;
    private readonly SemaphoreSlim _mutex = new(1, 1);
    private List<McpDispatchIntent>? _cache;

    public McpDispatchJournal(string path)
    {
        _path = path;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
    }

    public async Task<McpDispatchIntent> RecordAsync(
        McpDispatchIntent intent,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(intent);
        await _mutex.WaitAsync(ct);
        try
        {
            var entries = Load();
            entries.RemoveAll(item => item.IntentId == intent.IntentId);
            entries.Add(intent);
            Persist(entries);
            return intent;
        }
        finally
        {
            _mutex.Release();
        }
    }

    public async Task UpdateAsync(McpDispatchIntent intent, CancellationToken ct = default) =>
        await RecordAsync(intent, ct);

    public async Task ResolveAsync(
        string intentId,
        McpOperationStatus resolution,
        string reason,
        string? returnedObjectId = null,
        CancellationToken ct = default)
    {
        await _mutex.WaitAsync(ct);
        try
        {
            var entries = Load();
            var index = entries.FindIndex(item => item.IntentId == intentId);
            if (index < 0)
                return;
            entries[index] = entries[index] with
            {
                Resolution = resolution,
                ResolutionReason = reason,
                ResolvedUtc = DateTimeOffset.UtcNow,
                Phase = McpDispatchPhase.Recorded,
                ReturnedObjectId = returnedObjectId ?? entries[index].ReturnedObjectId
            };
            Persist(entries);
        }
        finally
        {
            _mutex.Release();
        }
    }

    public async Task<IReadOnlyList<McpDispatchIntent>> GetOpenAsync(
        string? connectorId = null,
        CancellationToken ct = default)
    {
        await _mutex.WaitAsync(ct);
        try
        {
            return Load()
                .Where(item => item.IsOpen)
                .Where(item => connectorId is null
                               || string.Equals(item.ConnectorId, connectorId,
                                   StringComparison.Ordinal))
                .OrderBy(item => item.CreatedUtc)
                .ToList();
        }
        finally
        {
            _mutex.Release();
        }
    }

    public async Task<McpDispatchIntent?> FindOpenByFingerprintAsync(
        string connectorId,
        string argumentsFingerprint,
        CancellationToken ct = default)
    {
        await _mutex.WaitAsync(ct);
        try
        {
            return Load().FirstOrDefault(item =>
                item.IsOpen
                && string.Equals(item.ConnectorId, connectorId, StringComparison.Ordinal)
                && string.Equals(item.ArgumentsFingerprint, argumentsFingerprint,
                    StringComparison.Ordinal));
        }
        finally
        {
            _mutex.Release();
        }
    }

    public async Task<McpDispatchIntent?> GetAsync(string intentId, CancellationToken ct = default)
    {
        await _mutex.WaitAsync(ct);
        try
        {
            return Load().FirstOrDefault(item => item.IntentId == intentId);
        }
        finally
        {
            _mutex.Release();
        }
    }

    private List<McpDispatchIntent> Load()
    {
        if (_cache is not null)
            return _cache;
        try
        {
            if (File.Exists(_path))
                return _cache = JsonSerializer.Deserialize<List<McpDispatchIntent>>(
                    File.ReadAllText(_path), JsonOptions) ?? [];
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            // A journal we cannot read is preserved, not overwritten: it is the
            // only record of what may already have happened.
            AtomicFile.TryBackupCorrupt(_path);
        }
        return _cache = [];
    }

    private void Persist(List<McpDispatchIntent> entries)
    {
        _cache = entries;
        AtomicFile.Write(_path, JsonSerializer.Serialize(entries, JsonOptions));
    }
}
