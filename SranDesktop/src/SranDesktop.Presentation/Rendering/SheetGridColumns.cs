using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Builds the spreadsheet grid's columns (Phase 16).
///
/// They cannot be declared in XAML: a sheet's shape is only known once the
/// file has been read, and it changes when the user switches sheets. Each
/// data column binds to <see cref="PreviewRowViewModel"/>'s INDEXER
/// (<c>Path=[i]</c>) — a DataGridTextColumn needs one binding per column and
/// there is no property to name.
///
/// Extracted from the dialog so a test can apply these to a REAL DataGrid and
/// read the rendered cells back. The Canvas hotfix earlier in this branch
/// shipped green tests over an empty pane because every assertion was about
/// configuration rather than output; a column set that throws while sealing,
/// or a binding path that resolves to nothing, looks identical to a working
/// one until something renders it.
/// </summary>
public static class SheetGridColumns
{
    /// <summary>The row-number gutter plus one column per sheet column.</summary>
    public static IReadOnlyList<DataGridColumn> Build(IReadOnlyList<string> letters)
    {
        var columns = new List<DataGridColumn>(letters.Count + 1)
        {
            // The sheet's own numbering, kept true across pages so a cell on
            // screen can be found in Excel by the same address.
            new DataGridTextColumn
            {
                Header = string.Empty,
                Binding = new Binding(nameof(PreviewRowViewModel.Label)),
                IsReadOnly = true,
                CellStyle = RowNumberCellStyle(),
            },
        };

        for (var i = 0; i < letters.Count; i++)
        {
            columns.Add(new DataGridTextColumn
            {
                Header = letters[i],
                // Mode=OneTime: a preview is read-only and a row never mutates
                // in place, so this also saves a change listener per cell.
                Binding = new Binding($"[{i}]") { Mode = BindingMode.OneTime },
                IsReadOnly = true,
                MaxWidth = 320,
            });
        }
        return columns;
    }

    /// <summary>
    /// Muted, right-aligned styling for the row-number gutter.
    ///
    /// HorizontalContentAlignment, not TextBlock.TextAlignment: a Style setter
    /// must name a property that exists on the target type, and TextAlignment
    /// is neither declared on DataGridCell nor attached — that setter throws
    /// while the style is sealed, i.e. the first time a sheet is shown.
    /// </summary>
    private static Style RowNumberCellStyle()
    {
        var style = new Style(typeof(DataGridCell));
        style.Setters.Add(new Setter(
            Control.HorizontalContentAlignmentProperty, HorizontalAlignment.Right));
        style.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(4, 0, 6, 0)));
        // The gutter is a label, not data: it must not take selection.
        style.Setters.Add(new Setter(UIElement.IsHitTestVisibleProperty, false));
        style.Seal();
        return style;
    }
}
