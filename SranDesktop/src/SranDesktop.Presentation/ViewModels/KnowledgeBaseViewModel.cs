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

/// <summary>In-app knowledge base management (R7 Feature D).</summary>
public partial class KnowledgeBaseViewModel : ObservableObject
{
    private readonly IChatEdgeApiClient _api;
    private readonly ILogger<KnowledgeBaseViewModel> _logger;

    public ObservableCollection<KnowledgeBaseItemViewModel> KnowledgeBases { get; } = new();

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsListEmpty))]
    private bool _hasLoaded;

    public bool IsListEmpty => HasLoaded && KnowledgeBases.Count == 0;

    // ── inline create panel ─────────────────────────────────────────────────
    [ObservableProperty]
    private bool _isCreatePanelOpen;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(CreateCommand))]
    private string _newName = string.Empty;

    [ObservableProperty]
    private string _newDescription = string.Empty;

    private readonly IDialogService _dialogs;

    /// <summary>Themed confirm/error dialogs (Phase C.5), shared with item VMs.</summary>
    internal IDialogService Dialogs => _dialogs;

    public KnowledgeBaseViewModel(IChatEdgeApiClient api, ILogger<KnowledgeBaseViewModel> logger, IDialogService dialogs)
    {
        _api = api;
        _logger = logger;
        _dialogs = dialogs;
    }

    [RelayCommand]
    public async Task LoadAsync()
    {
        IsLoading = true;
        ErrorMessage = null;
        try
        {
            var response = await _api.ListKnowledgeBasesDetailedAsync();
            KnowledgeBases.Clear();
            foreach (var kb in response.KnowledgeBases)
                KnowledgeBases.Add(new KnowledgeBaseItemViewModel(this, _api, _logger, kb));
            HasLoaded = true;
            OnPropertyChanged(nameof(IsListEmpty));
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "KB list failed");
            ErrorMessage = LocaleService.Instance.T("kb.loadFailed");
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    private void ToggleCreatePanel() => IsCreatePanelOpen = !IsCreatePanelOpen;

    private bool CanCreate() => !string.IsNullOrWhiteSpace(NewName);

    [RelayCommand(CanExecute = nameof(CanCreate))]
    private async Task CreateAsync()
    {
        ErrorMessage = null;
        try
        {
            var created = await _api.CreateKnowledgeBaseAsync(
                NewName.Trim(),
                string.IsNullOrWhiteSpace(NewDescription) ? null : NewDescription.Trim());
            KnowledgeBases.Insert(0, new KnowledgeBaseItemViewModel(this, _api, _logger, created));
            OnPropertyChanged(nameof(IsListEmpty));
            NewName = string.Empty;
            NewDescription = string.Empty;
            IsCreatePanelOpen = false;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "KB create failed");
            ErrorMessage = ex.Message;
        }
    }

    internal void Remove(KnowledgeBaseItemViewModel item)
    {
        KnowledgeBases.Remove(item);
        OnPropertyChanged(nameof(IsListEmpty));
    }
}

/// <summary>One knowledge base card with an expandable document panel.</summary>
public partial class KnowledgeBaseItemViewModel : ObservableObject
{
    private readonly KnowledgeBaseViewModel _parent;
    private readonly IChatEdgeApiClient _api;
    private readonly ILogger _logger;

    /// <summary>Themed dialogs (Phase C.5), forwarded from the list VM to the
    /// document child VMs.</summary>
    internal IDialogService Dialogs => _parent.Dialogs;

    public string Id { get; }

    [ObservableProperty] private string _name;
    [ObservableProperty] private string? _description;
    [ObservableProperty] private int _documentCount;
    [ObservableProperty] private int _totalChunks;
    [ObservableProperty] private bool _isActive;

    public bool HasDescription => !string.IsNullOrEmpty(Description);

    public ObservableCollection<KbDocumentViewModel> Documents { get; } = new();

    [ObservableProperty] private bool _isExpanded;
    [ObservableProperty] private bool _isLoadingDocuments;
    [ObservableProperty] private bool _isUploading;
    [ObservableProperty] private double _uploadProgress;

    public KnowledgeBaseItemViewModel(
        KnowledgeBaseViewModel parent, IChatEdgeApiClient api, ILogger logger,
        KnowledgeBaseDetailDto dto)
    {
        _parent = parent;
        _api = api;
        _logger = logger;
        Id = dto.Id;
        _name = dto.Name ?? "";
        _description = dto.Description;
        _documentCount = dto.DocumentCount;
        _totalChunks = dto.TotalChunks;
        _isActive = dto.IsActive;
    }

    /// <summary>"Manage" — expand and (re)load documents.</summary>
    [RelayCommand]
    private async Task ManageAsync()
    {
        IsExpanded = !IsExpanded;
        if (IsExpanded)
            await LoadDocumentsAsync();
    }

    [RelayCommand]
    public async Task LoadDocumentsAsync()
    {
        IsLoadingDocuments = true;
        try
        {
            var detail = await _api.GetKnowledgeBaseAsync(Id);
            Documents.Clear();
            foreach (var doc in detail.Documents)
                Documents.Add(new KbDocumentViewModel(this, _api, _logger, doc));
            DocumentCount = detail.KnowledgeBase.DocumentCount;
            TotalChunks = detail.KnowledgeBase.TotalChunks;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "KB detail failed for {Id}", Id);
        }
        finally
        {
            IsLoadingDocuments = false;
        }
    }

    [RelayCommand]
    private async Task ToggleActiveAsync()
    {
        var target = !IsActive;
        IsActive = target; // optimistic
        try
        {
            await _api.UpdateKnowledgeBaseAsync(Id, isActive: target);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            IsActive = !target;
            _logger.LogWarning(ex, "KB toggle failed for {Id}", Id);
        }
    }

    [RelayCommand]
    private async Task DeleteAsync()
    {
        var confirmed = await _parent.Dialogs.ConfirmAsync(
            LocaleService.Instance.T("kb.title"),
            LocaleService.Instance.T("kb.deleteConfirm", Name));
        if (!confirmed)
            return;

        try
        {
            await _api.DeleteKnowledgeBaseAsync(Id);
            _parent.Remove(this);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "KB delete failed for {Id}", Id);
        }
    }

    [RelayCommand]
    private async Task UploadDocumentAsync()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Title = LocaleService.Instance.T("kb.uploadDocument"),
            Filter = "Documents (*.pdf;*.docx;*.txt;*.md;*.csv)|*.pdf;*.docx;*.txt;*.md;*.csv|All files (*.*)|*.*"
        };
        if (dialog.ShowDialog() != true)
            return;

        IsUploading = true;
        UploadProgress = 0;
        try
        {
            var path = dialog.FileName;
            await using var stream = File.OpenRead(path);
            var progress = new Progress<double>(p => UploadProgress = p);
            var doc = await _api.UploadKbDocumentAsync(
                Id, stream, Path.GetFileName(path), GetContentType(path), stream.Length, progress);
            Documents.Add(new KbDocumentViewModel(this, _api, _logger, doc));
            DocumentCount++;
            IsExpanded = true;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException)
        {
            _logger.LogWarning(ex, "KB upload failed for {Id}", Id);
            _parent.Dialogs.NotifyError(ex.Message);
        }
        finally
        {
            IsUploading = false;
        }
    }

    internal void RemoveDocument(KbDocumentViewModel doc)
    {
        Documents.Remove(doc);
        DocumentCount = Math.Max(0, DocumentCount - 1);
    }

    private static string GetContentType(string path) => Path.GetExtension(path).ToLowerInvariant() switch
    {
        ".pdf" => "application/pdf",
        ".docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt" => "text/plain",
        ".md" => "text/markdown",
        ".csv" => "text/csv",
        _ => "application/octet-stream"
    };
}

/// <summary>One document row inside an expanded KB card.</summary>
public partial class KbDocumentViewModel : ObservableObject
{
    private readonly KnowledgeBaseItemViewModel _parent;
    private readonly IChatEdgeApiClient _api;
    private readonly ILogger _logger;

    public string Id { get; }
    public string Filename { get; }
    public string SizeText { get; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(StatusLabel))]
    [NotifyPropertyChangedFor(nameof(IsIndexed))]
    [NotifyPropertyChangedFor(nameof(IsFailed))]
    private string _status;

    [ObservableProperty] private int _chunkCount;
    [ObservableProperty] private bool _isBusy;

    public bool IsIndexed => Status == "indexed";
    public bool IsFailed => Status == "failed";

    public string StatusLabel => LocaleService.Instance.T(Status switch
    {
        "indexed" => "kb.status.indexed",
        "failed" => "kb.status.failed",
        _ => "kb.status.processing"
    });

    public KbDocumentViewModel(
        KnowledgeBaseItemViewModel parent, IChatEdgeApiClient api, ILogger logger, KbDocumentDto dto)
    {
        _parent = parent;
        _api = api;
        _logger = logger;
        Id = dto.Id;
        Filename = dto.Filename;
        _status = dto.Status ?? "pending";
        _chunkCount = dto.ChunkCount;
        SizeText = dto.FileSizeBytes switch
        {
            >= 1024 * 1024 => $"{dto.FileSizeBytes / (1024.0 * 1024.0):0.#} MB",
            >= 1024 => $"{dto.FileSizeBytes / 1024.0:0.#} KB",
            _ => $"{dto.FileSizeBytes} B"
        };
    }

    [RelayCommand]
    private async Task ReindexAsync()
    {
        IsBusy = true;
        try
        {
            var updated = await _api.ReindexKbDocumentAsync(_parent.Id, Id);
            Status = updated.Status ?? "processing";
            ChunkCount = updated.ChunkCount;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Reindex failed for doc {Id}", Id);
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand]
    private async Task DeleteAsync()
    {
        var confirmed = await _parent.Dialogs.ConfirmAsync(
            LocaleService.Instance.T("kb.title"),
            LocaleService.Instance.T("kb.deleteDocConfirm", Filename));
        if (!confirmed)
            return;

        IsBusy = true;
        try
        {
            await _api.DeleteKbDocumentAsync(_parent.Id, Id);
            _parent.RemoveDocument(this);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Doc delete failed for {Id}", Id);
        }
        finally
        {
            IsBusy = false;
        }
    }
}
