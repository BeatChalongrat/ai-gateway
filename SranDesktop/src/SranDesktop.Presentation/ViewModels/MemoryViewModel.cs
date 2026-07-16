using System.Collections.ObjectModel;
using System.IO;
using System.Net.Http;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>In-app memory management (R7 Feature E).</summary>
public partial class MemoryViewModel : ObservableObject
{
    private readonly IChatEdgeApiClient _api;
    private readonly ILogger<MemoryViewModel> _logger;
    private readonly List<MemoryItemViewModel> _allMemories = new();

    public ObservableCollection<MemoryItemViewModel> Memories { get; } = new();
    public ObservableCollection<MemoryItemViewModel> PendingMemories { get; } = new();

    [ObservableProperty] private string _searchText = string.Empty;
    [ObservableProperty] private bool _isLoading;
    [ObservableProperty] private string? _errorMessage;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(SavedHeader))]
    private int _totalCount;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasPending))]
    [NotifyPropertyChangedFor(nameof(PendingHeader))]
    private int _pendingCount;

    public bool HasPending => PendingCount > 0;

    public string SavedHeader => $"{LocaleService.Instance.T("memory.saved")} ({TotalCount})";
    public string PendingHeader => $"{LocaleService.Instance.T("memory.suggested.title")} ({PendingCount})";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsListEmpty))]
    private bool _hasLoaded;

    public bool IsListEmpty => HasLoaded && Memories.Count == 0;

    /// <summary>Raised after any mutation so the header badge can refresh.</summary>
    public event EventHandler? MemoriesChanged;

    private readonly IDialogService _dialogs;

    /// <summary>Themed confirm/error dialogs (Phase C.5), shared with item VMs.</summary>
    internal IDialogService Dialogs => _dialogs;

    public MemoryViewModel(IChatEdgeApiClient api, ILogger<MemoryViewModel> logger, IDialogService dialogs)
    {
        _api = api;
        _logger = logger;
        _dialogs = dialogs;
    }

    partial void OnSearchTextChanged(string value) => ApplyFilter();

    [RelayCommand]
    public async Task LoadAsync()
    {
        IsLoading = true;
        ErrorMessage = null;
        try
        {
            var active = await _api.ListMemoriesAsync(status: "active", limit: 200);
            var pending = await _api.ListMemoriesAsync(status: "pending_confirmation", limit: 50);

            _allMemories.Clear();
            foreach (var dto in active.Memories)
                _allMemories.Add(new MemoryItemViewModel(this, _api, _logger, dto));

            PendingMemories.Clear();
            foreach (var dto in pending.Memories)
                PendingMemories.Add(new MemoryItemViewModel(this, _api, _logger, dto));

            TotalCount = active.Total;
            PendingCount = pending.Memories.Count;
            HasLoaded = true;
            ApplyFilter();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory list failed");
            ErrorMessage = LocaleService.Instance.T("memory.loadFailed");
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void ApplyFilter()
    {
        var query = SearchText.Trim();
        Memories.Clear();
        foreach (var item in _allMemories)
        {
            if (query.Length == 0
                || item.Content.Contains(query, StringComparison.OrdinalIgnoreCase)
                || (item.Category ?? "").Contains(query, StringComparison.OrdinalIgnoreCase))
                Memories.Add(item);
        }
        OnPropertyChanged(nameof(IsListEmpty));
    }

    [RelayCommand]
    private async Task AddMemoryAsync()
    {
        var dialog = new Views.Dialogs.MemoryFormDialog
        {
            Owner = System.Windows.Application.Current.MainWindow
        };
        if (dialog.ShowDialog() != true)
            return;

        try
        {
            var created = await _api.CreateMemoryAsync(
                dialog.MemoryContent, dialog.MemoryType, dialog.Category);
            var vm = new MemoryItemViewModel(this, _api, _logger, created);
            _allMemories.Insert(0, vm);
            TotalCount++;
            ApplyFilter();
            NotifyChanged();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory create failed");
            ErrorMessage = ex.Message;
        }
    }

    [RelayCommand]
    private async Task ExportAsync()
    {
        try
        {
            var json = await _api.ExportMemoriesAsync();
            var dialog = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "JSON (*.json)|*.json",
                FileName = $"memories-export-{DateTime.Now:yyyyMMdd}.json"
            };
            if (dialog.ShowDialog() == true)
                await File.WriteAllTextAsync(dialog.FileName, json);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException)
        {
            _logger.LogWarning(ex, "Memory export failed");
            ErrorMessage = ex.Message;
        }
    }

    [RelayCommand]
    private async Task ClearAllAsync()
    {
        var confirmed = await _dialogs.ConfirmAsync(
            LocaleService.Instance.T("memory.danger.clearButton"),
            LocaleService.Instance.T("memory.danger.clearConfirm"));
        if (!confirmed)
            return;

        try
        {
            await _api.ClearAllMemoriesAsync();
            await LoadAsync();
            NotifyChanged();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory clear-all failed");
            ErrorMessage = ex.Message;
        }
    }

    [RelayCommand]
    private async Task PurgeAsync()
    {
        // GDPR purge: the user must type the confirmation phrase.
        var typed = Views.Dialogs.InputDialog.Show(
            System.Windows.Application.Current.MainWindow,
            LocaleService.Instance.T("memory.danger.purgeButton"),
            LocaleService.Instance.T("memory.danger.purgeConfirm"));
        if (typed is null
            || typed.Trim() != LocaleService.Instance.T("memory.danger.purgePhrase"))
            return;

        try
        {
            await _api.PurgeMemoriesAsync();
            await LoadAsync();
            NotifyChanged();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory purge failed");
            ErrorMessage = ex.Message;
        }
    }

    internal void RemoveItem(MemoryItemViewModel item)
    {
        if (_allMemories.Remove(item))
            TotalCount = Math.Max(0, TotalCount - 1);
        Memories.Remove(item);
        if (PendingMemories.Remove(item))
            PendingCount = Math.Max(0, PendingCount - 1);
        OnPropertyChanged(nameof(IsListEmpty));
        NotifyChanged();
    }

    /// <summary>A pending memory was confirmed — move it into the saved list.</summary>
    internal void PromoteToActive(MemoryItemViewModel item)
    {
        if (PendingMemories.Remove(item))
            PendingCount = Math.Max(0, PendingCount - 1);
        _allMemories.Insert(0, item);
        TotalCount++;
        ApplyFilter();
        NotifyChanged();
    }

    internal void NotifyChanged() => MemoriesChanged?.Invoke(this, EventArgs.Empty);
}

/// <summary>One memory row (saved or pending).</summary>
public partial class MemoryItemViewModel : ObservableObject
{
    private readonly MemoryViewModel _parent;
    private readonly IChatEdgeApiClient _api;
    private readonly ILogger _logger;

    public string Id { get; }

    [ObservableProperty] private string _content;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(TypeLabel))]
    [NotifyPropertyChangedFor(nameof(MetaLine))]
    private string _memoryType;

    [ObservableProperty] private string? _category;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(PinLabel))]
    private bool _isPinned;

    public string Status { get; private set; }
    public DateTimeOffset CreatedAt { get; }
    public int UseCount { get; private set; }

    public string PinLabel => LocaleService.Instance.T(IsPinned ? "memory.unpin" : "memory.pin");

    /// <summary>Short label: explicit_fact → Fact, response_preference → Preference, ...</summary>
    public string TypeLabel => LocaleService.Instance.T(MemoryType switch
    {
        "explicit_fact" => "memory.type.fact",
        "response_preference" or "inferred_preference" => "memory.type.preference",
        "ongoing_interest" => "memory.type.interest",
        "confirmed_instruction" => "memory.type.instruction",
        _ => "memory.type.fact"
    });

    public string MetaLine
    {
        get
        {
            var parts = new List<string> { TypeLabel, CreatedAt.LocalDateTime.ToString("d MMM yyyy") };
            if (UseCount > 0)
                parts.Add(LocaleService.Instance.T("memory.usedTimes", UseCount));
            return string.Join(" · ", parts);
        }
    }

    public MemoryItemViewModel(
        MemoryViewModel parent, IChatEdgeApiClient api, ILogger logger, MemoryDto dto)
    {
        _parent = parent;
        _api = api;
        _logger = logger;
        Id = dto.Id;
        _content = dto.Content;
        _memoryType = dto.MemoryType ?? "explicit_fact";
        _category = dto.Category;
        _isPinned = dto.IsPinned;
        Status = dto.Status ?? "active";
        CreatedAt = dto.CreatedAt;
        UseCount = dto.UseCount;
    }

    [RelayCommand]
    private async Task EditAsync()
    {
        var dialog = new Views.Dialogs.MemoryFormDialog(Content, MemoryType, Category)
        {
            Owner = System.Windows.Application.Current.MainWindow
        };
        if (dialog.ShowDialog() != true)
            return;

        try
        {
            var updated = await _api.UpdateMemoryAsync(
                Id, dialog.MemoryContent, dialog.MemoryType, dialog.Category ?? "");
            Content = updated.Content;
            MemoryType = updated.MemoryType ?? MemoryType;
            Category = updated.Category;
            _parent.NotifyChanged();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory update failed for {Id}", Id);
        }
    }

    [RelayCommand]
    private async Task TogglePinAsync()
    {
        var target = !IsPinned;
        IsPinned = target; // optimistic
        try
        {
            await _api.PinMemoryAsync(Id, target);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            IsPinned = !target;
            _logger.LogWarning(ex, "Memory pin failed for {Id}", Id);
        }
    }

    [RelayCommand]
    private async Task DeleteAsync()
    {
        var confirmed = await _parent.Dialogs.ConfirmAsync(
            LocaleService.Instance.T("memory.title"),
            LocaleService.Instance.T("memory.deleteConfirm"));
        if (!confirmed)
            return;

        try
        {
            await _api.DeleteMemoryAsync(Id);
            _parent.RemoveItem(this);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory delete failed for {Id}", Id);
        }
    }

    /// <summary>Pending suggestion → confirm and promote to the saved list.</summary>
    [RelayCommand]
    private async Task ConfirmAsync()
    {
        try
        {
            await _api.ConfirmMemoryAsync(Id, true);
            Status = "active";
            _parent.PromoteToActive(this);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory confirm failed for {Id}", Id);
        }
    }

    /// <summary>Pending suggestion → reject and drop.</summary>
    [RelayCommand]
    private async Task RejectAsync()
    {
        try
        {
            await _api.ConfirmMemoryAsync(Id, false);
            _parent.RemoveItem(this);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Memory reject failed for {Id}", Id);
        }
    }
}
