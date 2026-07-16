using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Storage;

/// <summary>JSON-file project store: %APPDATA%\SRAN Desktop\projects.json +
/// per-project file folders (R9 Feature B, local-first).</summary>
public sealed class JsonProjectStore : IProjectStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    private readonly string _root;
    private readonly string _indexFile;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private List<Project>? _cache;

    public JsonProjectStore() : this(Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "SRAN Desktop"))
    {
    }

    public JsonProjectStore(string root)
    {
        _root = root;
        _indexFile = Path.Combine(root, "projects.json");
    }

    public async Task<IReadOnlyList<Project>> GetAllAsync(CancellationToken ct = default)
    {
        await _lock.WaitAsync(ct);
        try
        {
            return Load();
        }
        finally
        {
            _lock.Release();
        }
    }

    public async Task UpsertAsync(Project project, CancellationToken ct = default)
    {
        await _lock.WaitAsync(ct);
        try
        {
            var all = Load();
            var index = all.FindIndex(p => p.Id == project.Id);
            project.UpdatedAt = DateTimeOffset.UtcNow;
            if (index >= 0) all[index] = project;
            else all.Add(project);
            Save(all);
        }
        finally
        {
            _lock.Release();
        }
    }

    public async Task DeleteAsync(string id, CancellationToken ct = default)
    {
        await _lock.WaitAsync(ct);
        try
        {
            var all = Load();
            all.RemoveAll(p => p.Id == id);
            Save(all);
            var folder = ProjectFolder(id);
            if (Directory.Exists(folder))
                Directory.Delete(folder, recursive: true);
        }
        finally
        {
            _lock.Release();
        }
    }

    public async Task<string> AddFileAsync(Project project, string sourcePath, CancellationToken ct = default)
    {
        var filesDir = Path.Combine(ProjectFolder(project.Id), "files");
        Directory.CreateDirectory(filesDir);
        var target = Path.Combine(filesDir, Path.GetFileName(sourcePath));
        File.Copy(sourcePath, target, overwrite: true);
        if (!project.Files.Contains(target))
            project.Files.Add(target);
        await UpsertAsync(project, ct);
        return target;
    }

    public async Task RemoveFileAsync(Project project, string storedPath, CancellationToken ct = default)
    {
        project.Files.Remove(storedPath);
        try
        {
            if (File.Exists(storedPath))
                File.Delete(storedPath);
        }
        catch (IOException)
        {
            // Locked file — the index entry is gone either way.
        }
        await UpsertAsync(project, ct);
    }

    public async Task<Project?> FindByConversationAsync(string conversationId, CancellationToken ct = default)
    {
        var all = await GetAllAsync(ct);
        return all.FirstOrDefault(p => p.ConversationIds.Contains(conversationId));
    }

    private string ProjectFolder(string id) => Path.Combine(_root, "projects", id);

    private List<Project> Load()
    {
        if (_cache is not null)
            return _cache;
        try
        {
            if (File.Exists(_indexFile))
                return _cache = JsonSerializer.Deserialize<List<Project>>(
                    File.ReadAllText(_indexFile)) ?? new();
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            // Corrupt index — start fresh rather than crash.
        }
        return _cache = new List<Project>();
    }

    private void Save(List<Project> all)
    {
        _cache = all;
        Directory.CreateDirectory(_root);
        AtomicFile.Write(_indexFile, JsonSerializer.Serialize(all, JsonOptions));
    }
}
