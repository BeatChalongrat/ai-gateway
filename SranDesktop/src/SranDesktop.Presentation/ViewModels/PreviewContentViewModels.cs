using System.Windows;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// One row of the spreadsheet grid (Phase 16).
///
/// Cells are exposed through an INDEXER because the columns are not known
/// until the file is read: the grid generates one bound column per sheet
/// column at load time (Path=[0], [1], …). A row shorter than the sheet is
/// normal — a trailing empty cell is stored nowhere in the file — so an
/// out-of-range index reads as blank rather than throwing during layout.
///
/// The text is never markup. It reaches the screen through a WPF
/// TextBlock, which renders a string as a string; there is no HTML surface
/// here for a cell to escape from.
/// </summary>
public sealed class PreviewRowViewModel
{
    private readonly IReadOnlyList<string> _cells;

    public PreviewRowViewModel(string label, IReadOnlyList<string>? cells)
    {
        Label = label;
        _cells = cells ?? Array.Empty<string>();
    }

    /// <summary>Excel-style row number, true to the file even after paging.</summary>
    public string Label { get; }

    public string this[int index] =>
        index >= 0 && index < _cells.Count ? _cells[index] ?? string.Empty : string.Empty;

    public int CellCount => _cells.Count;
}

/// <summary>One entry in the sheet tab strip.</summary>
public sealed class SheetTabViewModel
{
    public SheetTabViewModel(int index, string name, bool isSelected)
    {
        Index = index;
        Name = name;
        IsSelected = isSelected;
    }

    public int Index { get; }
    public string Name { get; }
    public bool IsSelected { get; }

    /// <summary>Bound directly rather than through a Style with a DataTrigger:
    /// an inline Style would have to say BasedOn="{StaticResource {x:Type
    /// Button}}" to keep the app's button look, and that pulls a parse-time
    /// dependency on the application resource dictionary into a dialog. One
    /// bound property keeps the implicit style applying by itself.</summary>
    public FontWeight TabFontWeight => IsSelected ? FontWeights.SemiBold : FontWeights.Normal;
}

/// <summary>
/// A heading / paragraph / bullet / table from a Word document or a slide.
///
/// The visual weight is computed here rather than through a stack of XAML
/// triggers: the mapping from "heading level 2" to a font size is one rule,
/// and it belongs in one place.
/// </summary>
public sealed class PreviewBlockViewModel
{
    public PreviewBlockViewModel(PreviewBlockDto block)
    {
        var type = (block.Type ?? "paragraph").ToLowerInvariant();
        Text = block.Text ?? string.Empty;
        IsTable = type == "table";
        IsBullet = type == "list";

        if (IsTable)
        {
            TableRows = (block.Rows ?? new List<List<string>>())
                .Select(r => (IReadOnlyList<string>)(r ?? new List<string>()))
                .ToList();
        }

        if (type == "heading")
        {
            var level = Math.Clamp(block.Level <= 0 ? 1 : block.Level, 1, 6);
            FontSize = level switch { 1 => 19, 2 => 16.5, 3 => 15, _ => 13.5 };
            IsBold = true;
            Margin = new Thickness(0, level == 1 ? 14 : 10, 0, 4);
        }
        else
        {
            FontSize = 13;
            IsBold = false;
            // Bullets indent by their nesting level; a paragraph does not.
            Margin = new Thickness(
                IsBullet ? 12 + Math.Clamp(block.Level, 0, 5) * 14 : 0, 3, 0, 3);
        }
    }

    public string Text { get; }
    public bool IsTable { get; }
    public bool IsBullet { get; }
    public bool IsBold { get; }
    public double FontSize { get; }
    public Thickness Margin { get; }

    /// <summary>Populated only for a table block.</summary>
    public IReadOnlyList<IReadOnlyList<string>> TableRows { get; } =
        Array.Empty<IReadOnlyList<string>>();

    /// <summary>Text and table are mutually exclusive; the pane shows one.</summary>
    public bool IsText => !IsTable;
}

/// <summary>One slide panel: its number, its title, and its body blocks.</summary>
public sealed class SlidePanelViewModel
{
    public SlidePanelViewModel(SlidePreviewDto slide, int totalSlides)
    {
        Index = slide.Index;
        Title = slide.Title ?? string.Empty;
        Blocks = (slide.Blocks ?? new List<PreviewBlockDto>())
            .Select(b => new PreviewBlockViewModel(b))
            .ToList();
        Caption = Localization.LocaleService.Instance.T("preview.slideN")
            .Replace("{n}", Index.ToString())
            .Replace("{total}", totalSlides.ToString());
    }

    public int Index { get; }
    public string Title { get; }
    public string Caption { get; }
    public bool HasTitle => Title.Length > 0;
    public IReadOnlyList<PreviewBlockViewModel> Blocks { get; }

    /// <summary>A slide with neither a title nor text — a picture-only slide.
    /// Said plainly rather than shown as an empty box.</summary>
    public bool IsEmpty => !HasTitle && Blocks.Count == 0;
}
