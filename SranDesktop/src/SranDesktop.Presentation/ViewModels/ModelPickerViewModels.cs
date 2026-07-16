using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One row in the model picker: the DTO plus selection + badge state.</summary>
public partial class ModelItemViewModel : ObservableObject
{
    public ModelInfo Model { get; }

    [ObservableProperty]
    private bool _isSelected;

    public string DisplayName => Model.DisplayName;
    public string? Provider => Model.Provider;
    public bool SupportsImage => Model.SupportsImage;
    public bool SupportsThinking => Model.SupportsThinking;
    public bool SupportsVideo => Model.SupportsVideo;
    public bool SupportsAudio => Model.SupportsAudio;

    // Phase C PART 4: surface per-model info that already exists in the data but
    // wasn't rendered — the provider + context window, and a one-line description.
    public string? Description => Model.Description;
    public bool HasDescription => !string.IsNullOrWhiteSpace(Model.Description);

    /// <summary>"Provider · 128K context" — whichever parts are present.</summary>
    public string? MetaLine
    {
        get
        {
            var parts = new List<string>();
            if (!string.IsNullOrEmpty(Provider)) parts.Add(Provider!);
            if (Model.ContextWindow is > 0) parts.Add(FormatContext(Model.ContextWindow!.Value));
            return parts.Count > 0 ? string.Join(" · ", parts) : null;
        }
    }

    public bool HasMetaLine => MetaLine is not null;

    private static string FormatContext(int tokens) => tokens switch
    {
        >= 1_000_000 => $"{tokens / 1_000_000}M context",
        >= 1_000 => $"{tokens / 1_000}K context",
        _ => $"{tokens} context",
    };

    public ModelItemViewModel(ModelInfo model, bool isSelected)
    {
        Model = model;
        _isSelected = isSelected;
    }
}

/// <summary>A picker section: POPULAR / RECENTLY USED / one provider.</summary>
public partial class ModelGroup : ObservableObject
{
    public string Header { get; init; } = string.Empty;

    /// <summary>Model count shown on collapsible provider sections.</summary>
    public int? Count { get; init; }

    /// <summary>Provider sections collapse; Popular/Recently Used don't.</summary>
    public bool IsCollapsible { get; init; }

    [ObservableProperty]
    private bool _isExpanded = true;

    public List<ModelItemViewModel> Models { get; init; } = new();

    [RelayCommand]
    private void ToggleExpand() => IsExpanded = !IsExpanded;
}
