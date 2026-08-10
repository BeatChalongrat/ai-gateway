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
    public bool SupportsImage => Model.SupportsImage;
    public bool SupportsThinking => Model.SupportsThinking;
    public bool SupportsVideo => Model.SupportsVideo;
    public bool SupportsAudio => Model.SupportsAudio;

    // Phase C PART 4: surface the one-line description the catalogue already
    // sends but nothing rendered.
    //
    // Phase 13.1 removed the meta line that used to sit above it (provider ·
    // context window · price per 1M). SRAN is an internal tool on a shared
    // account — nobody picking a model here is making a purchase decision, so
    // token economics was noise. The provider is still visible as the picker's
    // section header, and the catalogue still serves the numbers on ModelInfo;
    // they are simply not rendered.
    public string? Description => Model.Description;
    public bool HasDescription => !string.IsNullOrWhiteSpace(Model.Description);

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
