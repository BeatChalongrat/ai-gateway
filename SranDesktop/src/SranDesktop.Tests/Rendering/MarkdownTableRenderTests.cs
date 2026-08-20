using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using FluentAssertions;
using SranDesktop.Presentation.Controls;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Batch 1 D3 — markdown tables must be readable, not scroll tunnels.
///
/// The old renderer gave every Grid column Width=Auto: Auto measures its
/// children at INFINITE width, so a prose cell never wrapped, the GFM
/// alignment row was ignored, and ragged rows were truncated at the header's
/// column count. These render the REAL element tree (WPF is thread-affine →
/// each body runs on its own STA thread, same pattern as
/// SheetGridRenderTests) and read the layout back.
/// </summary>
public class MarkdownTableRenderTests
{
    private static void RunSta(Action action)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try { action(); }
            catch (Exception ex) { failure = ex; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure is not null)
            throw failure;
    }

    /// <summary>Renders markdown, lays it out at the message column's width,
    /// and returns the table's Grid.</summary>
    private static Grid RenderTableGrid(string markdown)
    {
        var root = new MarkdownRenderer().Render(markdown);
        root.Measure(new Size(768, double.PositiveInfinity));
        root.Arrange(new Rect(root.DesiredSize));
        return FindDescendant<Grid>(root)
            ?? throw new InvalidOperationException("no table Grid rendered");
    }

    /// <summary>Logical-tree walk over the containers the renderer emits
    /// (panels, borders, scroll viewers) — no window needed.</summary>
    private static T? FindDescendant<T>(object? node) where T : class
    {
        switch (node)
        {
            case null:
                return null;
            case T match:
                return match;
            case Panel panel:
                foreach (var child in panel.Children)
                    if (FindDescendant<T>(child) is { } fromPanel)
                        return fromPanel;
                return null;
            case Decorator decorator:
                return FindDescendant<T>(decorator.Child);
            case ContentControl content:
                return FindDescendant<T>(content.Content);
            default:
                return null;
        }
    }

    /// <summary>The renderer fills TextBlocks via Inlines (Runs), which the
    /// Text property getter does not surface — read the runs directly.</summary>
    private static string TextOf(TextBlock tb) =>
        string.Concat(tb.Inlines.OfType<Run>().Select(r => r.Text));

    private static List<TextBlock> CellTextBlocks(Grid grid, int row, int column)
    {
        var result = new List<TextBlock>();
        foreach (var child in grid.Children)
        {
            if (child is Border border
                && Grid.GetRow(border) == row && Grid.GetColumn(border) == column
                && border.Child is Panel panel)
            {
                foreach (var inner in panel.Children)
                    if (inner is TextBlock tb)
                        result.Add(tb);
            }
        }
        return result;
    }

    [Fact]
    public void Long_Prose_Cell_Wraps_Instead_Of_Growing_The_Column() => RunSta(() =>
    {
        var prose = string.Join(" ", Enumerable.Repeat(
            "a real comparison table holds sentences like this one", 4));
        var grid = RenderTableGrid($"| Feature | Notes |\n|---|---|\n| short | {prose} |");

        var shortCell = CellTextBlocks(grid, 1, 0).Single();
        var proseCell = CellTextBlocks(grid, 1, 1).Single();

        // The capped column forces the wrap: the prose TextBlock lays out
        // multi-line (taller than the single-line sibling) instead of one
        // endless line.
        proseCell.DesiredSize.Width.Should().BeLessThanOrEqualTo(
            MarkdownRenderer.MaxTableCellWidth);
        proseCell.DesiredSize.Height.Should().BeGreaterThan(
            shortCell.DesiredSize.Height * 2);

        // And the table as a whole fits the message column — no scroll tunnel.
        grid.DesiredSize.Width.Should().BeLessThanOrEqualTo(
            2 * MarkdownRenderer.MaxTableCellWidth);
    });

    [Fact]
    public void Alignment_Markers_Set_Cell_Text_Alignment() => RunSta(() =>
    {
        var grid = RenderTableGrid(
            "| left | mid | right |\n|:--|:-:|--:|\n| 1 | 2 | 3 |");

        CellTextBlocks(grid, 1, 0).Single().TextAlignment.Should().Be(TextAlignment.Left);
        CellTextBlocks(grid, 1, 1).Single().TextAlignment.Should().Be(TextAlignment.Center);
        CellTextBlocks(grid, 1, 2).Single().TextAlignment.Should().Be(TextAlignment.Right);
    });

    [Fact]
    public void Ragged_Rows_Render_Their_Extra_Cells() => RunSta(() =>
    {
        // Two declared columns, a data row with four cells — the extras used
        // to be silently discarded (`break` at the header's column count).
        var grid = RenderTableGrid(
            "| a | b |\n|---|---|\n| 1 | 2 | 3 | 4 |");

        grid.ColumnDefinitions.Count.Should().Be(4);
        TextOf(CellTextBlocks(grid, 1, 2).Single()).Should().Be("3");
        TextOf(CellTextBlocks(grid, 1, 3).Single()).Should().Be("4");
    });

    [Fact]
    public void A_Compact_Numeric_Table_Still_Autosizes_Below_The_Cap() => RunSta(() =>
    {
        // The cap must not inflate small tables — Auto sizing below the cap
        // is unchanged.
        var grid = RenderTableGrid("| n | v |\n|---|---|\n| 1 | 2 |");

        grid.DesiredSize.Width.Should().BeLessThan(MarkdownRenderer.MaxTableCellWidth);
    });
}
