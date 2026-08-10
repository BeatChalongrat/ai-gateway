using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One row in the version rail.</summary>
public sealed record CanvasVersionRow(int Version, string Content, string Type, DateTimeOffset CreatedAt)
{
    public string Label => $"v{Version}";
    public string When => CreatedAt.ToLocalTime().ToString("d MMM HH:mm");
}

/// <summary>
/// The Canvas panel (Phase 15) — preview, edit, and version history for one
/// artifact.
///
/// Versioning is SERVER-backed through the same canvas_artifacts endpoints the
/// web canvas uses, so an edit made here appears on the web and vice versa.
/// The lineage key is derived identically on both clients
/// (<see cref="CanvasEligibility.ArtifactKeyFor"/>) — that shared derivation is
/// what makes the two histories one history instead of two.
///
/// Rendering itself is the view's job (a WebView2 under
/// <see cref="Rendering.CanvasSandbox"/>); this view model owns only the source
/// of truth and when a re-render is due, which keeps every rule below testable
/// without a browser.
/// </summary>
public partial class CanvasViewModel : ObservableObject
{
    /// <summary>Quiet period after a keystroke before the preview re-renders.
    /// Long enough that typing does not thrash the browser, short enough that
    /// the edit still feels live.</summary>
    public static readonly TimeSpan RerenderDebounce = TimeSpan.FromMilliseconds(400);

    private readonly IChatEdgeApiClient? _api;
    private readonly ILogger _logger;
    private readonly string? _conversationId;
    private readonly string? _messageId;

    public string ArtifactKey { get; }
    public string Title { get; }

    public CanvasViewModel(
        IChatEdgeApiClient? api,
        ILogger logger,
        string type,
        string source,
        string title,
        string? conversationId,
        string? messageId)
    {
        _api = api;
        _logger = logger;
        _conversationId = conversationId;
        _messageId = messageId;
        _type = type;
        _source = source;
        _draft = source;
        Title = string.IsNullOrWhiteSpace(title) ? CanvasEligibility.DownloadName(type, null) : title;
        // Derived from the ORIGINAL source: later edits must still resolve to
        // the same lineage, or every save would start a new history.
        ArtifactKey = CanvasEligibility.ArtifactKeyFor(messageId, source);
    }

    /// <summary>Versioning needs a real server conversation. An artifact from
    /// a local-only agent chat still previews and edits in-session; it just has
    /// nowhere to persist.</summary>
    public bool CanPersist => _api is not null && !string.IsNullOrEmpty(_conversationId);

    [ObservableProperty]
    private string _type;

    /// <summary>The committed source — what the preview renders.</summary>
    [ObservableProperty]
    private string _source;

    /// <summary>The editor's text. Diverges from <see cref="Source"/> while the
    /// user types and is committed on debounce.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsDirty))]
    private string _draft;

    /// <summary>Code view instead of preview. Opt-in: opening a canvas shows
    /// the rendered result, which is what the user asked for.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsPreviewMode))]
    private bool _isCodeMode;

    public bool IsPreviewMode => !IsCodeMode;

    /// <summary>Unsaved edits exist.</summary>
    public bool IsDirty => !string.Equals(Draft, Source, StringComparison.Ordinal);

    [ObservableProperty]
    private bool _isBusy;

    /// <summary>Runtime error reported by the artifact itself (uncaught
    /// exception / rejected promise), surfaced so a broken artifact is not a
    /// silent blank pane.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasRuntimeError))]
    private string? _runtimeError;

    public bool HasRuntimeError => !string.IsNullOrEmpty(RuntimeError);

    [ObservableProperty]
    private string? _statusMessage;

    public ObservableCollection<CanvasVersionRow> Versions { get; } = new();

    public bool HasVersions => Versions.Count > 0;

    /// <summary>Raised when the preview should be rebuilt from
    /// <see cref="Source"/>. The view owns the WebView2; this is the signal.</summary>
    public event EventHandler? RenderRequested;

    /// <summary>Commits the draft and asks for a re-render. Called by the view
    /// once the debounce elapses — kept here so "what a commit does" is
    /// testable without a timer.</summary>
    public void CommitDraft()
    {
        if (!IsDirty)
            return;
        Source = Draft;
        RuntimeError = null; // stale error belongs to the previous source
        RenderRequested?.Invoke(this, EventArgs.Empty);
        OnPropertyChanged(nameof(IsDirty));
    }

    /// <summary>Records a validated runtime error from the sandbox.</summary>
    public void ReportRuntimeError(string message) =>
        RuntimeError = string.IsNullOrWhiteSpace(message)
            ? LocaleService.Instance.T("canvas.runtimeError")
            : message;

    public void ReportReady() => RuntimeError = null;

    [RelayCommand]
    private void ToggleCodeMode() => IsCodeMode = !IsCodeMode;

    [RelayCommand]
    private void CopySource()
    {
        try
        {
            System.Windows.Clipboard.SetText(Draft);
            StatusMessage = LocaleService.Instance.T("canvas.copied");
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Clipboard copy failed");
        }
    }

    [RelayCommand]
    private void Download()
    {
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            FileName = CanvasEligibility.DownloadName(Type, Title),
            Filter = "All files (*.*)|*.*",
        };
        if (dialog.ShowDialog() != true)
            return;
        try
        {
            System.IO.File.WriteAllText(dialog.FileName, Draft);
        }
        catch (Exception ex) when (ex is System.IO.IOException or UnauthorizedAccessException)
        {
            _logger.LogWarning(ex, "Canvas download failed");
            StatusMessage = LocaleService.Instance.T("canvas.saveFailed");
        }
    }

    // ── Versioning (server-backed, shared with the web) ─────────────────────

    /// <summary>
    /// Loads the lineage. When the server has nothing for this key, the block
    /// the user just opened is ADOPTED as version 1 — the same rule the web
    /// applies. Without adoption nothing server-side identifies the artifact,
    /// so it could never be revised from the other client.
    /// </summary>
    public async Task LoadVersionsAsync()
    {
        if (!CanPersist)
            return;
        IsBusy = true;
        try
        {
            var response = await _api!.ListCanvasVersionsAsync(_conversationId!, ArtifactKey);
            var versions = response.Versions ?? new List<CanvasArtifactDto>();

            if (versions.Count == 0)
            {
                await AdoptInitialVersionAsync();
                return;
            }

            ReplaceVersions(versions);

            // Adopt the newest saved content — but NEVER over unsaved edits:
            // the user typing must win over a background history load.
            var latest = versions[0];
            if (!IsDirty)
            {
                Type = latest.Type;
                Source = latest.Content;
                Draft = latest.Content;
                RenderRequested?.Invoke(this, EventArgs.Empty);
            }
        }
        catch (Exception ex)
        {
            // History is an enhancement — failing to load it must not break
            // the preview the user actually came for.
            _logger.LogWarning(ex, "Could not load canvas versions");
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task AdoptInitialVersionAsync()
    {
        if (!CanPersist || string.IsNullOrEmpty(Source))
            return;
        try
        {
            var saved = await _api!.SaveCanvasArtifactAsync(_conversationId!,
                new SaveCanvasArtifactRequest(ArtifactKey, Type, Title, Source, _messageId ?? string.Empty));
            ReplaceVersions(new List<CanvasArtifactDto> { saved });
        }
        catch (Exception ex)
        {
            // Best effort: previewing and in-session editing still work.
            _logger.LogDebug(ex, "Canvas adopt-on-open skipped");
        }
    }

    /// <summary>Saves the current draft as the next version.</summary>
    [RelayCommand]
    private async Task SaveVersionAsync()
    {
        if (!CanPersist || IsBusy)
            return;
        CommitDraft();
        IsBusy = true;
        StatusMessage = null;
        try
        {
            var saved = await _api!.SaveCanvasArtifactAsync(_conversationId!,
                new SaveCanvasArtifactRequest(ArtifactKey, Type, Title, Source, _messageId ?? string.Empty));
            Versions.Insert(0, ToRow(saved));
            OnPropertyChanged(nameof(HasVersions));
            StatusMessage = LocaleService.Instance.T("canvas.saved");
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not save canvas version");
            StatusMessage = LocaleService.Instance.T("canvas.saveFailed");
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>
    /// Reverts to an earlier version. The server APPENDS it as a new version
    /// rather than deleting anything, so an accidental revert is itself
    /// revertable.
    /// </summary>
    [RelayCommand]
    private async Task RevertAsync(object? parameter)
    {
        // FIX 12: reachable from a templated list row — take object? and match.
        if (parameter is not CanvasVersionRow row || !CanPersist || IsBusy)
            return;
        IsBusy = true;
        StatusMessage = null;
        try
        {
            var reverted = await _api!.RevertCanvasArtifactAsync(_conversationId!, ArtifactKey, row.Version);
            Versions.Insert(0, ToRow(reverted));
            OnPropertyChanged(nameof(HasVersions));
            Type = reverted.Type;
            Source = reverted.Content;
            Draft = reverted.Content;
            RuntimeError = null;
            OnPropertyChanged(nameof(IsDirty));
            RenderRequested?.Invoke(this, EventArgs.Empty);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not revert canvas artifact");
            StatusMessage = LocaleService.Instance.T("canvas.revertFailed");
        }
        finally
        {
            IsBusy = false;
        }
    }

    private void ReplaceVersions(IReadOnlyList<CanvasArtifactDto> versions)
    {
        Versions.Clear();
        foreach (var v in versions)
            Versions.Add(ToRow(v));
        OnPropertyChanged(nameof(HasVersions));
    }

    private static CanvasVersionRow ToRow(CanvasArtifactDto dto) =>
        new(dto.Version, dto.Content, dto.Type, dto.CreatedAt);
}
