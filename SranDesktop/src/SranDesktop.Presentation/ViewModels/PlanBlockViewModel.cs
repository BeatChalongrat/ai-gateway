using System.Net.Http;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// One :::excel-plan / :::doc-plan / :::pptx-plan fence parsed out of an
/// assistant message (R7 Feature C). Executes via chat-edge and swaps itself
/// into a result card. When Settings → "generate locally" is on and the plan
/// is fully supported on-device, execution runs through the matching
/// <see cref="ILocalPlanExecutor"/> instead (Phase 12 E); any local failure
/// or unsupported plan falls back to the chat-edge path transparently.
/// </summary>
public partial class PlanBlockViewModel : ObservableObject
{
    private readonly IChatEdgeApiClient _api;
    private readonly string _conversationId;
    private readonly JsonElement _plan;
    private readonly ILocalPlanExecutor? _localExecutor;
    private readonly ISettingsService? _settings;
    private readonly ILogger? _logger;

    /// <summary>"excel" | "doc" | "pptx".</summary>
    public string PlanType { get; }

    public string? Description { get; }
    public string? OutputFilename { get; }
    public IReadOnlyList<string> Actions { get; }

    public bool HasDescription => !string.IsNullOrEmpty(Description);

    public string Title => LocaleService.Instance.T(PlanType switch
    {
        "excel" => "plan.excel.title",
        "pptx" => "plan.pptx.title",
        _ => "plan.doc.title"
    });

    public string ActionsHeader => LocaleService.Instance.T("plan.changes", Actions.Count);

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(ExecuteCommand))]
    private bool _isExecuting;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasResult))]
    private PlanResultViewModel? _result;

    public bool HasResult => Result is not null;

    [ObservableProperty]
    private string? _errorMessage;

    public PlanBlockViewModel(
        IChatEdgeApiClient api, string conversationId, string planType, JsonElement plan,
        ILocalPlanExecutor? localExecutor = null, ISettingsService? settings = null,
        ILogger? logger = null)
    {
        _api = api;
        _conversationId = conversationId;
        _plan = plan;
        _localExecutor = localExecutor;
        _settings = settings;
        _logger = logger;
        PlanType = planType;

        Description = GetString(plan, "description");
        OutputFilename = GetString(plan, "output_filename");
        Actions = SummarizeActions(planType, plan);
    }

    private bool CanExecute() => !IsExecuting && Result is null;

    [RelayCommand(CanExecute = nameof(CanExecute))]
    private async Task ExecuteAsync()
    {
        IsExecuting = true;
        ErrorMessage = null;
        try
        {
            if (await TryExecuteLocallyAsync())
                return;

            var sourceFileId = GetString(_plan, "source_file_id");
            var result = PlanType == "excel"
                ? await _api.ExecuteExcelPlanAsync(_conversationId,
                    new ExecuteExcelPlanRequest(sourceFileId, OutputFilename, _plan))
                : await _api.ExecuteDocumentPlanAsync(_conversationId,
                    new ExecuteDocumentPlanRequest(
                        sourceFileId,
                        PlanType == "pptx" ? "pptx" : GetString(_plan, "format") ?? "docx",
                        OutputFilename, _plan));

            Result = new PlanResultViewModel(_api, result);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            ErrorMessage = $"{LocaleService.Instance.T("plan.failed")}: {ex.Message}";
        }
        finally
        {
            IsExecuting = false;
        }
    }

    /// <summary>
    /// On-device execution attempt (Phase 12 E). True = handled (Result set);
    /// false = caller must run the server path — either the setting is off,
    /// the plan isn't fully supported locally, or local execution failed
    /// (logged; the fallback keeps the click working transparently).
    /// </summary>
    private async Task<bool> TryExecuteLocallyAsync()
    {
        if (_settings?.Current.PreferLocalOfficeGeneration != true
            || _localExecutor is null || !_localExecutor.CanExecute(_plan))
            return false;

        try
        {
            // Staged under temp until the user clicks Download — the same UX
            // as the server path, where bytes only land via the save dialog.
            var outputPath = System.IO.Path.Combine(
                System.IO.Path.GetTempPath(), "SranDesktop", "plans",
                Guid.NewGuid().ToString("N"), BuildLocalOutputFilename());
            var local = await _localExecutor.ExecuteAsync(_plan, outputPath);

            // Version 0 on purpose: local files sit outside the server's
            // DB-backed version lineage (a stated tradeoff of the setting).
            Result = new PlanResultViewModel(
                _api,
                new PlanExecutionResult(
                    new PlanFileDto(
                        "local", System.IO.Path.GetFileName(local.OutputPath), null,
                        local.FileSizeBytes, null, 0),
                    new PlanValidationDto(true, null, null, null, local.Stats),
                    local.Changes),
                local.OutputPath);
            return true;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger?.LogWarning(ex,
                "Local {PlanType} plan execution failed — falling back to chat-edge",
                PlanType);
            return false;
        }
    }

    private string BuildLocalOutputFilename()
    {
        var ext = PlanType switch
        {
            "excel" => ".xlsx",
            "pptx" => ".pptx",
            _ => ".docx",
        };
        var name = System.IO.Path.GetFileName(OutputFilename ?? "").Trim();
        if (string.IsNullOrEmpty(name) || name == ext)
            name = "output" + ext;
        if (!name.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            name += ext;
        return name;
    }

    /// <summary>Human-readable one-liners for the card's action list.</summary>
    private static IReadOnlyList<string> SummarizeActions(string planType, JsonElement plan)
    {
        var items = new List<string>();
        var arrayName = planType == "pptx" ? "slides" : "actions";
        if (plan.ValueKind != JsonValueKind.Object
            || !plan.TryGetProperty(arrayName, out var array)
            || array.ValueKind != JsonValueKind.Array)
            return items;

        foreach (var entry in array.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object)
                continue;
            var type = GetString(entry, "type") ?? "step";
            var detail = GetString(entry, "sheet_name")
                         ?? GetString(entry, "title")
                         ?? GetString(entry, "text")
                         ?? GetString(entry, "range");
            if (detail is { Length: > 60 })
                detail = detail[..60] + "…";
            items.Add(detail is null
                ? type.Replace('_', ' ')
                : $"{type.Replace('_', ' ')} — {detail}");
        }
        return items;
    }

    private static string? GetString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
}

/// <summary>Result card state after a successful plan execution.</summary>
public partial class PlanResultViewModel : ObservableObject
{
    private readonly IChatEdgeApiClient _api;
    private readonly PlanFileDto? _file;

    public string Filename { get; }
    public string MetaLine { get; }
    public IReadOnlyList<string> Changes { get; }

    /// <summary>Set when the file was generated on-device (Phase 12 E):
    /// Download copies this staged file instead of fetching from chat-edge.</summary>
    public string? LocalFilePath { get; }

    [ObservableProperty]
    private bool _isDownloading;

    public PlanResultViewModel(
        IChatEdgeApiClient api, PlanExecutionResult result, string? localFilePath = null)
    {
        _api = api;
        _file = result.File;
        LocalFilePath = localFilePath;

        Filename = _file is null
            ? "output"
            : _file.Version > 0 ? $"{_file.Filename} (v{_file.Version})" : _file.Filename;

        var parts = new List<string>();
        if (_file is not null)
            parts.Add(FormatSize(_file.FileSizeBytes));
        var stats = result.Validation?.Stats;
        if (stats is not null)
        {
            if (stats.TryGetValue("sheets", out var sheets))
                parts.Add($"{sheets} {LocaleService.Instance.T("plan.result.sheets")}");
            if (stats.TryGetValue("slides", out var slides))
                parts.Add($"{slides} {LocaleService.Instance.T("plan.result.slides")}");
            if (stats.TryGetValue("charts", out var charts) && charts > 0)
                parts.Add($"{charts} {LocaleService.Instance.T("plan.result.charts")}");
            if (stats.TryGetValue("page_count", out var pages) && pages > 0)
                parts.Add($"{pages} pages");
        }
        MetaLine = string.Join("  ·  ", parts);
        Changes = result.Changes ?? new List<string>();
    }

    public bool HasChanges => Changes.Count > 0;

    private static string FormatSize(long bytes) => bytes switch
    {
        >= 1024 * 1024 => $"{bytes / (1024.0 * 1024.0):0.#} MB",
        >= 1024 => $"{bytes / 1024.0:0.#} KB",
        _ => $"{bytes} B"
    };

    [RelayCommand]
    private async Task DownloadAsync()
    {
        if (_file is null || (_file.DownloadUrl is null && LocalFilePath is null))
            return;

        var ext = System.IO.Path.GetExtension(_file.Filename);
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            FileName = _file.Filename,
            Filter = string.IsNullOrEmpty(ext)
                ? "All files (*.*)|*.*"
                : $"{ext.TrimStart('.').ToUpperInvariant()} (*{ext})|*{ext}|All files (*.*)|*.*"
        };
        if (dialog.ShowDialog() != true)
            return;

        IsDownloading = true;
        try
        {
            if (LocalFilePath is not null)
            {
                // Locally generated file — copy out of the temp staging dir.
                var source = LocalFilePath;
                await Task.Run(() => System.IO.File.Copy(source, dialog.FileName, true));
            }
            else
            {
                var bytes = await _api.DownloadFileAsync(_file.DownloadUrl!);
                await System.IO.File.WriteAllBytesAsync(dialog.FileName, bytes);
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or System.IO.IOException)
        {
            Application.Services.ToastService.Instance.Show(
                Application.Interfaces.ToastKind.Error, "dialog.error", ex.Message);
        }
        finally
        {
            IsDownloading = false;
        }
    }
}
