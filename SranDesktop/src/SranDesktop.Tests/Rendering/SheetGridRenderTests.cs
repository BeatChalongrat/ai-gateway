using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using FluentAssertions;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Phase 16 — proof that the spreadsheet grid actually DRAWS.
///
/// The Canvas hotfix earlier in this branch is the reason this file exists:
/// that suite asserted configuration (CSP strings, host names, policy) and
/// stayed green while every canvas showed an empty pane. The equivalent trap
/// here is a column set that throws while its style seals, or an indexer
/// binding that silently resolves to nothing — a view-model test cannot tell
/// either apart from a working grid.
///
/// So these put the REAL columns on a REAL DataGrid inside a shown (but
/// off-screen) window and read the rendered cells back. WPF elements are
/// thread-affine, so each body runs on its own STA thread.
/// </summary>
public class SheetGridRenderTests
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

    /// <summary>Builds a live grid and returns the text of every rendered cell,
    /// row by row — exactly what a person would read off the screen.</summary>
    private static List<List<string>> Render(
        IReadOnlyList<string> columns, IReadOnlyList<PreviewRowViewModel> rows)
    {
        var grid = new DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            CanUserAddRows = false,
            HeadersVisibility = DataGridHeadersVisibility.Column,
            ItemsSource = rows,
        };
        foreach (var column in SheetGridColumns.Build(columns))
            grid.Columns.Add(column);

        // Really shown: WPF does not generate containers, and therefore does
        // not evaluate a single cell binding, for a window that never renders.
        var window = new Window
        {
            Width = 900,
            Height = 400,
            Left = -10000,
            Top = -10000,
            ShowInTaskbar = false,
            WindowStyle = WindowStyle.None,
            Content = grid,
        };
        try
        {
            window.Show();
            grid.UpdateLayout();

            var rendered = new List<List<string>>();
            foreach (var item in rows)
            {
                if (grid.ItemContainerGenerator.ContainerFromItem(item) is not DataGridRow row)
                    continue;
                row.ApplyTemplate();
                var presenter = FindChild<DataGridCellsPresenter>(row);
                if (presenter is null)
                    continue;
                presenter.UpdateLayout();

                var cells = new List<string>();
                for (var i = 0; i < grid.Columns.Count; i++)
                {
                    var cell = presenter.ItemContainerGenerator.ContainerFromIndex(i) as DataGridCell;
                    cells.Add((cell?.Content as TextBlock)?.Text ?? string.Empty);
                }
                rendered.Add(cells);
            }
            return rendered;
        }
        finally
        {
            window.Close();
        }
    }

    private static T? FindChild<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = VisualTreeHelper.GetChild(parent, i);
            if (child is T match)
                return match;
            if (FindChild<T>(child) is { } deeper)
                return deeper;
        }
        return null;
    }

    [Fact]
    public void The_Cells_Of_A_Sheet_Reach_The_Screen() => RunSta(() =>
    {
        var rendered = Render(
            new[] { "A", "B", "C" },
            new[]
            {
                new PreviewRowViewModel("1", new[] { "ชื่อ", "จำนวน", "วันที่" }),
                new PreviewRowViewModel("2", new[] { "สมชาย", "12", "2026-08-06" }),
            });

        rendered.Should().HaveCount(2);
        // The gutter carries the row number; the data follows in order.
        rendered[0].Should().Equal("1", "ชื่อ", "จำนวน", "วันที่");
        rendered[1].Should().Equal("2", "สมชาย", "12", "2026-08-06");
    });

    [Fact]
    public void Row_Numbers_Survive_Paging() => RunSta(() =>
    {
        // The second page's rows are labelled from the ACCUMULATED offset, so
        // row 201 reads as 201 rather than restarting at 1.
        var rendered = Render(
            new[] { "A" },
            new[] { new PreviewRowViewModel("201", new[] { "แถวที่ 201" }) });

        rendered[0][0].Should().Be("201");
        rendered[0][1].Should().Be("แถวที่ 201");
    });

    [Fact]
    public void A_Row_Shorter_Than_The_Sheet_Renders_Blanks_Not_A_Crash() => RunSta(() =>
    {
        // Trailing empty cells are stored nowhere in the file, so short rows
        // are normal. Every column still has to draw.
        var rendered = Render(
            new[] { "A", "B", "C" },
            new[] { new PreviewRowViewModel("1", new[] { "only one" }) });

        rendered[0].Should().Equal("1", "only one", "", "");
    });

    [Fact]
    public void A_Cell_Containing_Markup_Is_Drawn_As_Text() => RunSta(() =>
    {
        // The security assertion, made where it counts: on screen. A WPF
        // TextBlock draws a string as a string — the cell must appear
        // verbatim and produce no element of its own.
        const string payload = "<img src=x onerror=alert(1)>";

        var rendered = Render(new[] { "A" }, new[] { new PreviewRowViewModel("1", new[] { payload }) });

        rendered[0][1].Should().Be(payload);
    });

    [Fact]
    public void A_Wide_Sheet_Builds_Every_Column_It_Was_Given() => RunSta(() =>
    {
        var letters = Enumerable.Range(0, 60).Select(i => $"C{i}").ToArray();

        var columns = SheetGridColumns.Build(letters);

        // One gutter + one per sheet column.
        columns.Should().HaveCount(61);
        columns[0].Header.Should().Be(string.Empty);
        columns[1].Header.Should().Be("C0");
    });

    [Fact]
    public void An_Empty_Sheet_Still_Builds_A_Usable_Grid() => RunSta(() =>
    {
        var rendered = Render(Array.Empty<string>(), Array.Empty<PreviewRowViewModel>());

        rendered.Should().BeEmpty();
    });

    [Fact]
    public void The_Preview_Window_Itself_Parses_And_Shows_The_Grid() => RunSta(() =>
    {
        // The whole dialog, built the way the app builds it. A XamlParseException
        // in the new markup — a bad template, a resource that only exists in the
        // application dictionary — would only ever surface when a user clicks a
        // file, which is exactly the failure mode this branch already shipped
        // once with the Canvas.
        var vm = new FilePreviewViewModel(
            "member_no_part_003.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            () => Task.FromResult<byte[]?>(Array.Empty<byte>()),
            () => Task.FromResult(new SranDesktop.Application.Models.FileTextDto(
                "completed", "fallback", false, false)),
            Microsoft.Extensions.Logging.Abstractions.NullLogger.Instance,
            (_, _, _) => Task.FromResult(new SranDesktop.Application.Models.FilePreviewDataDto(
                "sheet",
                new SranDesktop.Application.Models.SheetPreviewDto(
                    new List<string> { "ยอดขาย", "Notes" }, 0, "ยอดขาย",
                    new List<string> { "A", "B" },
                    new List<List<string>> { new() { "สมชาย", "12" } },
                    0, 500, true, false),
                null, null)));

        var dialog = new SranDesktop.Presentation.Views.Dialogs.FilePreviewDialog(vm)
        {
            Left = -10000,
            Top = -10000,
            ShowInTaskbar = false,
        };
        try
        {
            dialog.Show();
            // Loaded fires the async load; pump until the grid has its rows.
            for (var i = 0; i < 50 && vm.SheetRows.Count == 0; i++)
            {
                dialog.Dispatcher.Invoke(
                    () => { }, System.Windows.Threading.DispatcherPriority.Background);
                Thread.Sleep(10);
            }
            dialog.UpdateLayout();

            vm.IsSheet.Should().BeTrue();
            var grid = FindChild<DataGrid>(dialog);
            grid.Should().NotBeNull("the workbook pane must be a real grid");
            // Gutter + A + B.
            grid!.Columns.Should().HaveCount(3);
            grid.Items.Count.Should().Be(1);
        }
        finally
        {
            dialog.Close();
        }
    });
}
