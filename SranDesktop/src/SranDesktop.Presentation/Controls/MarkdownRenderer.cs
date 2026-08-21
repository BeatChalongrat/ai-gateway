using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using Markdig;
using Markdig.Extensions.CustomContainers;
using Markdig.Extensions.Mathematics;
using Markdig.Extensions.Tables;
using Markdig.Extensions.TaskLists;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using SranDesktop.Presentation.Rendering;
using Block = Markdig.Syntax.Block;
using Table = Markdig.Extensions.Tables.Table;
using TableRow = Markdig.Extensions.Tables.TableRow;
using TableCell = Markdig.Extensions.Tables.TableCell;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// Renders GFM markdown into a WPF element tree (StackPanel of blocks).
/// Theme-aware: every color is attached via SetResourceReference so
/// light/dark switching restyles rendered messages live.
/// </summary>
public sealed class MarkdownRenderer
{
    private static readonly MarkdownPipeline Pipeline = BuildPipeline();

    private static MarkdownPipeline BuildPipeline()
    {
        var builder = new MarkdownPipelineBuilder()
            .UseAdvancedExtensions()
            // Math ($...$/$$...$$ → MathInline/MathBlock) ships inside
            // UseAdvancedExtensions; the explicit call is idempotent and pins
            // the dependency in case a future Markdig slims the advanced set.
            .UseMathematics();

        // UseAdvancedExtensions bundles CustomContainers, which owns the ':::'
        // grammar — the same marker this app uses for :::artifact and the
        // :::*-plan cards. Those are extracted from the raw text BEFORE
        // rendering (ChatViewModel.ExtractPlanBlocks), so by the time markdown
        // is parsed a ':::' can only be one the app did not recognise. Leaving
        // the extension on, Markdig consumed the marker into a CustomContainer
        // block that RenderBlock has no case for, so it fell through `_ => null`
        // and the user's text VANISHED — a malformed one-line directive
        // rendered as an empty bubble. Removing it makes an unrecognised ':::'
        // degrade to literal text, which is the only honest thing to show.
        builder.Extensions.RemoveAll(e => e is CustomContainerExtension);
        return builder.Build();
    }

    // Match web's JetBrains Mono vibe with fonts that ship on Windows.
    private static readonly FontFamily MonoFont =
        new("Cascadia Code, Cascadia Mono, JetBrains Mono, Consolas, Courier New");

    // Inline math + streamed math source: serif italic reads as math without
    // Cambria Math's oversized line metrics.
    private static readonly FontFamily MathFont = new("Cambria, Georgia, Segoe UI");

    private static readonly Geometry CopyGeometry = Geometry.Parse(
        "M20 8H10a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2Z " +
        "M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2");

    private static readonly Geometry CheckGeometry = Geometry.Parse("M20 6 9 17l-5-5");

    private static readonly Geometry ImageGeometry = Geometry.Parse(
        "M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2Z " +
        "M8.5 13.5 11 16.51 14.5 12 19 18H5l3.5-4.5Z");

    /// <summary>Parses with the renderer's exact pipeline — the streaming
    /// viewer must diff the SAME block structure this class renders.
    /// T1: GPT-style \[…\]/\(…\) math delimiters normalize to $-forms
    /// FIRST (fence/inline-code/streaming aware — see MathDelimiters),
    /// inside this one choke point so the viewer's block diff and the
    /// full render always see identical normalized structure.</summary>
    internal static MarkdownDocument ParseDocument(string markdown) =>
        Markdig.Markdown.Parse(MathDelimiters.Normalize(markdown), Pipeline);

    public FrameworkElement Render(string markdown)
    {
        var panel = new StackPanel();
        if (string.IsNullOrWhiteSpace(markdown))
            return panel;

        MarkdownDocument document;
        try
        {
            document = ParseDocument(markdown);
        }
        catch (Exception)
        {
            // Never let a malformed fragment (mid-stream) take down the UI.
            panel.Children.Add(PlainText(markdown));
            return panel;
        }

        foreach (var block in document)
        {
            var element = RenderBlock(block);
            if (element is not null)
                panel.Children.Add(element);
        }

        return panel;
    }

    /// <summary>Renders one top-level block — the streaming viewer's per-block
    /// path and the full-document path share this method so their output is
    /// identical. <paramref name="streaming"/> only relaxes the code-block
    /// highlight perf guard (see RenderCodeBlock).</summary>
    internal FrameworkElement? RenderBlock(Block block, bool streaming = false) => block switch
    {
        HeadingBlock heading => RenderHeading(heading),
        ParagraphBlock paragraph => RenderParagraph(paragraph, streaming),
        // MathBlock DERIVES from FencedCodeBlock — it must match first or
        // $$…$$ silently renders as a code block again.
        MathBlock math => RenderMathBlock(math, streaming),
        FencedCodeBlock code => RenderCodeBlock(code.Lines.ToString(), (code.Info ?? "").Trim(), streaming),
        CodeBlock code => RenderCodeBlock(code.Lines.ToString(), "", streaming),
        ListBlock list => RenderList(list, streaming),
        QuoteBlock quote => RenderQuote(quote, streaming),
        Table table => RenderTable(table, streaming),
        ThematicBreakBlock => RenderRule(),
        HtmlBlock html => PlainText(html.Lines.ToString()),
        _ => null
    };

    private TextBlock RenderHeading(HeadingBlock heading)
    {
        var text = NewTextBlock();
        text.FontWeight = FontWeights.SemiBold;
        text.FontSize = heading.Level switch
        {
            1 => 24,
            2 => 20,
            3 => 17,
            _ => 16
        };
        text.Margin = new Thickness(0, 18, 0, 8);
        if (heading.Inline is not null)
            AddInlines(text.Inlines, heading.Inline);
        return text;
    }

    private FrameworkElement RenderParagraph(ParagraphBlock paragraph, bool streaming = false)
    {
        // A paragraph that IS a single $$…$$ (the one-line display form
        // models usually emit) upgrades to the display-math surface. Not
        // while streaming — mid-stream it stays styled text, exactly like
        // RenderMathBlock (see MathGuard.ShouldRenderKatex for why).
        if (!streaming && MathGuard.AsDisplayMath(paragraph) is { } display)
            return RenderKatexBlock(display.Content.ToString());

        var text = NewTextBlock();
        text.Margin = new Thickness(0, 0, 0, 16);
        if (paragraph.Inline is not null)
            AddInlines(text.Inlines, paragraph.Inline);
        return text;
    }

    /// <summary>$$…$$ display block. A complete block in a FINISHED message
    /// renders through the bundled KaTeX in a sandboxed WebView2
    /// (<see cref="MathBlockControl"/>). While streaming — or while the
    /// closing $$ hasn't arrived — the TeX shows as styled plain text
    /// instead: the progressive renderer re-builds the trailing block every
    /// tick, and browser instances there would churn and flash half-typed
    /// math. The authoritative post-stream render swaps in KaTeX once.</summary>
    private FrameworkElement RenderMathBlock(MathBlock math, bool streaming)
    {
        var tex = math.Lines.ToString();
        return MathGuard.ShouldRenderKatex(math, streaming)
            ? RenderKatexBlock(tex)
            : RenderMathSourceText(tex);
    }

    /// <summary>
    /// Counts the browser surfaces this renderer has created. MathBlockControl
    /// owns a live WebView2, and a rendered tree containing one must NOT be
    /// held in the render cache: sixty retained entries would be sixty retained
    /// browser frames. MarkdownViewer reads this counter around a render to
    /// tell whether the tree it just built is cacheable.
    /// </summary>
    internal static int KatexSurfacesCreated;

    private static FrameworkElement RenderKatexBlock(string tex)
    {
        KatexSurfacesCreated++;
        return new MathBlockControl(tex) { Margin = new Thickness(0, 8, 0, 16) };
    }

    /// <summary>Streamed / unterminated math: readable styled text, never a
    /// half-typed KaTeX render.</summary>
    private static TextBlock RenderMathSourceText(string tex)
    {
        var text = NewTextBlock();
        text.Margin = new Thickness(0, 8, 0, 16);
        text.FontFamily = MathFont;
        text.FontStyle = FontStyles.Italic;
        text.Text = MathTex.ToInlineText(tex);
        return text;
    }

    /// <summary>Perf guards for syntax highlighting: a block above
    /// <see cref="StreamingHighlightLimit"/> renders plain WHILE STREAMING
    /// (highlighted on the final pass); above <see cref="HighlightLimit"/> it
    /// is never highlighted.</summary>
    private const int StreamingHighlightLimit = 20_000;
    private const int HighlightLimit = 200_000;

    private FrameworkElement RenderCodeBlock(string code, string language, bool streaming = false)
    {
        code = code.TrimEnd('\n', '\r');

        var codeText = new TextBlock
        {
            FontFamily = MonoFont,
            FontSize = 14,
            TextWrapping = TextWrapping.NoWrap
        };
        codeText.SetResourceReference(TextBlock.ForegroundProperty, "CodeTextBrush");

        var allowHighlight = code.Length <= HighlightLimit
                             && !(streaming && code.Length > StreamingHighlightLimit);
        var tokens = allowHighlight ? SyntaxHighlighter.Tokenize(code, language) : null;
        if (tokens is null)
        {
            // Unknown language or over the perf guard — single-color as before.
            codeText.Text = code;
        }
        else
        {
            foreach (var token in tokens)
            {
                var run = new Run(token.Text);
                // Plain runs inherit CodeTextBrush from the TextBlock; colored
                // kinds get their own theme-tracked brush reference.
                if (SyntaxBrushKey(token.Kind) is { } brushKey)
                    run.SetResourceReference(TextElement.ForegroundProperty, brushKey);
                codeText.Inlines.Add(run);
            }
        }

        var scroll = new ScrollViewer
        {
            Content = codeText,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
            Padding = new Thickness(0)
        };

        var header = new DockPanel();
        var langLabel = new TextBlock
        {
            Text = language.Length > 0 ? language : "code",
            FontSize = 11,
            VerticalAlignment = VerticalAlignment.Center
        };
        langLabel.SetResourceReference(TextBlock.ForegroundProperty, "TextSecondaryBrush");

        var copyIcon = new System.Windows.Shapes.Path
        {
            Data = CopyGeometry,
            Stretch = Stretch.Uniform,
            Width = 13,
            Height = 13,
            StrokeThickness = 2,
            StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round,
            StrokeLineJoin = PenLineJoin.Round
        };
        copyIcon.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty, "TextSecondaryBrush");

        var copyButton = new Button
        {
            Content = copyIcon,
            Padding = new Thickness(5),
            Cursor = System.Windows.Input.Cursors.Hand,
            HorizontalAlignment = HorizontalAlignment.Right,
            Background = Brushes.Transparent,
            BorderThickness = new Thickness(0),
            ToolTip = Localization.LocaleService.Instance.T("artifact.copyCode")
        };
        copyButton.Click += (_, _) =>
        {
            try
            {
                Clipboard.SetText(code);
                copyIcon.Data = CheckGeometry;
                copyIcon.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty, "AccentGreenBrush");
                var revert = new System.Windows.Threading.DispatcherTimer
                {
                    Interval = TimeSpan.FromSeconds(2)
                };
                revert.Tick += (_, _) =>
                {
                    copyIcon.Data = CopyGeometry;
                    copyIcon.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty, "TextSecondaryBrush");
                    revert.Stop();
                };
                revert.Start();
            }
            catch (Exception)
            {
                // Clipboard can be locked by another process — non-fatal.
            }
        };

        DockPanel.SetDock(copyButton, Dock.Right);
        header.Children.Add(copyButton);

        // Phase 15 — "Open in Canvas" for blocks that can actually render.
        // The inline code block stays the default view; this is an extra
        // affordance next to Copy, exactly as on the web.
        if (Application.Services.CanvasEligibility.CanOpen(language, code))
        {
            var canvasButton = new Button
            {
                Content = new TextBlock
                {
                    Text = Localization.LocaleService.Instance.T("canvas.open"),
                    FontSize = 11,
                },
                Padding = new Thickness(8, 3, 8, 3),
                Margin = new Thickness(0, 0, 6, 0),
                Cursor = System.Windows.Input.Cursors.Hand,
                Background = Brushes.Transparent,
                BorderThickness = new Thickness(0),
                ToolTip = Localization.LocaleService.Instance.T("canvas.open"),
            };
            ((TextBlock)canvasButton.Content).SetResourceReference(
                TextBlock.ForegroundProperty, "TextSecondaryBrush");
            var capturedCode = code;
            var capturedLang = language;
            canvasButton.Click += (sender, _) =>
                Services.CanvasLauncher.Open(sender as FrameworkElement, capturedLang, capturedCode);
            DockPanel.SetDock(canvasButton, Dock.Right);
            header.Children.Add(canvasButton);
        }

        header.Children.Add(langLabel);

        // Header strip (language + copy) carries its own theme-aware bg,
        // matching the web (dark #2D2D2D / light #EEE) atop the code body.
        var headerBar = new Border
        {
            Child = header,
            Padding = new Thickness(12, 5, 8, 5),
            CornerRadius = new CornerRadius(12, 12, 0, 0)
        };
        headerBar.SetResourceReference(Border.BackgroundProperty, "CodeHeaderBgBrush");

        var body = new Border
        {
            Child = scroll,
            Padding = new Thickness(12, 10, 12, 10),
            CornerRadius = new CornerRadius(0, 0, 12, 12)
        };
        body.SetResourceReference(Border.BackgroundProperty, "CodeBgBrush");

        var inner = new StackPanel();
        inner.Children.Add(headerBar);
        inner.Children.Add(body);

        var border = new Border
        {
            Child = inner,
            CornerRadius = new CornerRadius(12),
            BorderThickness = new Thickness(1),
            Margin = new Thickness(0, 12, 0, 12),
            Effect = new System.Windows.Media.Effects.DropShadowEffect
            {
                ShadowDepth = 1,
                BlurRadius = 3,
                Opacity = 0.2,
                Color = Colors.Black
            }
        };
        border.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
        return border;
    }

    private static string? SyntaxBrushKey(TokenKind kind) => kind switch
    {
        TokenKind.Keyword => "SynKeywordBrush",
        TokenKind.Type => "SynTypeBrush",
        TokenKind.String => "SynStringBrush",
        TokenKind.Comment => "SynCommentBrush",
        TokenKind.Number => "SynNumberBrush",
        _ => null
    };

    private FrameworkElement RenderList(ListBlock list, bool streaming = false)
    {
        // web: padding-left 1.4rem, margin 0.5rem 0 1rem.
        var panel = new StackPanel { Margin = new Thickness(22, 8, 0, 16) };
        var index = 1;
        if (list.IsOrdered && int.TryParse(list.OrderedStart, out var start))
            index = start;

        foreach (var item in list.OfType<ListItemBlock>())
        {
            // web: li margin 0.25rem 0 = 4px total gap between items.
            var row = new Grid { Margin = new Thickness(0, 2, 0, 2) };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

            // Small middle-dot bullet (web's disc renders subtle), not a big "•".
            var bulletText = list.IsOrdered ? $"{index}." : "·";
            // GFM task list items carry a TaskList inline at the start; the
            // checkbox is rendered by AddInlines, so keep the bullet neutral.
            var bullet = new TextBlock
            {
                Text = bulletText,
                FontSize = 14,
                Margin = new Thickness(0, 2, 6, 0),
                MinWidth = 12
            };
            bullet.SetResourceReference(TextBlock.ForegroundProperty, "TextSecondaryBrush");
            Grid.SetColumn(bullet, 0);
            row.Children.Add(bullet);

            var content = new StackPanel();
            foreach (var child in item)
            {
                var rendered = RenderBlock(child, streaming);
                if (rendered is not null)
                {
                    // web li > p margin ~0.15rem: much tighter than a standalone paragraph.
                    if (rendered is TextBlock tb)
                        tb.Margin = new Thickness(0, 2, 0, 2);
                    content.Children.Add(rendered);
                }
            }
            // Tighten list items: drop the trailing paragraph's bottom margin
            // (web's last-child margin 0) so single-line items don't look loose.
            if (content.Children.Count > 0 && content.Children[^1] is FrameworkElement lastChild)
                lastChild.Margin = new Thickness(lastChild.Margin.Left, lastChild.Margin.Top, lastChild.Margin.Right, 0);
            Grid.SetColumn(content, 1);
            row.Children.Add(content);

            panel.Children.Add(row);
            index++;
        }

        return panel;
    }

    private FrameworkElement RenderQuote(QuoteBlock quote, bool streaming = false)
    {
        var content = new StackPanel();
        foreach (var child in quote)
        {
            var rendered = RenderBlock(child, streaming);
            if (rendered is not null)
                content.Children.Add(rendered);
        }

        var border = new Border
        {
            Child = content,
            BorderThickness = new Thickness(3, 0, 0, 0),
            Padding = new Thickness(14, 6, 14, 6),
            Margin = new Thickness(0, 12, 0, 12),
            CornerRadius = new CornerRadius(0, 8, 8, 0)
        };
        border.SetResourceReference(Border.BorderBrushProperty, "TextTertiaryBrush");
        return border;
    }

    /// <summary>Fraction of the available message width one table column may
    /// occupy (Batch 1 D3's original intent). Two prose columns fit side by
    /// side; anything wider still falls back to the horizontal ScrollViewer.
    /// </summary>
    internal const double TableCellWidthFraction = 0.4;

    /// <summary>Width of the assistant message column (MessageBubble.xaml's
    /// StackPanel MaxWidth). Used as the assumed width before the table has
    /// been laid out — see <see cref="MaxTableCellWidthFor"/>.</summary>
    internal const double MessageColumnWidth = 768;

    /// <summary>Floor for a column cap. Below this a prose cell wraps every
    /// two or three words, which is harder to read than a scroll tunnel — so
    /// a very narrow window scrolls horizontally instead of shredding text.
    /// </summary>
    internal const double MinTableCellWidth = 160;

    /// <summary>Cap on a table column's width (Batch 1 D3). An Auto Grid
    /// column measures its children at infinite width, so without a cap a
    /// prose cell NEVER wraps and the whole table becomes a horizontal
    /// scroll tunnel.
    ///
    /// The cap scales with the width actually available to the table rather
    /// than the old fixed 300px, so a narrow window stops handing every
    /// column more room than the window has.
    ///
    /// Unusable widths (0 / NaN / Infinity — e.g. before layout) resolve to
    /// the full-column value, so a table measures the same before and after
    /// realization and cannot change height as it scrolls into view in the
    /// recycling ListBox.
    ///
    /// NOTE on why this takes a width instead of reading one at runtime: the
    /// message column is currently a FIXED 768px (ChatView.xaml sets
    /// MessageBubble Width="768"), so there is no live width to react to.
    /// Binding the cap to the table's own container would also be circular —
    /// that container auto-fits its content (HorizontalAlignment.Left), so
    /// cell caps would feed back into the width they were derived from. If
    /// the column ever becomes responsive, pass the real width here.
    /// </summary>
    internal static double MaxTableCellWidthFor(double availableWidth)
    {
        if (double.IsNaN(availableWidth) || double.IsInfinity(availableWidth)
            || availableWidth <= 0)
            return MessageColumnWidth * TableCellWidthFraction;

        return Math.Max(MinTableCellWidth, availableWidth * TableCellWidthFraction);
    }

    /// <summary>The cap in force for today's fixed message column: 40% of
    /// 768px. Replaces the hand-picked 300 with the number that constant was
    /// approximating.</summary>
    internal static readonly double MaxTableCellWidth =
        MaxTableCellWidthFor(MessageColumnWidth);

    private FrameworkElement RenderTable(Table table, bool streaming = false)
    {
        var grid = new Grid();
        // Derived once per table, not per cell: identical for every cell and
        // constant across layout passes, so nothing here can make a row
        // change height while the list recycles it.
        var cellCap = MaxTableCellWidthFor(MessageColumnWidth);

        var columnCount = table.ColumnDefinitions.Count;
        // D3 — ragged rows: a data row can carry MORE cells than the header
        // declared. They used to be silently truncated (content loss);
        // extend the grid to the widest row instead so every cell renders.
        var totalColumns = columnCount;
        foreach (var r in table.OfType<TableRow>())
            totalColumns = Math.Max(totalColumns, r.OfType<TableCell>().Count());
        for (var c = 0; c < totalColumns; c++)
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var rowIndex = 0;
        foreach (var row in table.OfType<TableRow>())
        {
            grid.RowDefinitions.Add(new RowDefinition());
            var colIndex = 0;
            foreach (var cell in row.OfType<TableCell>())
            {
                // D3 — honour the delimiter row's alignment markers
                // (`:--` / `:-:` / `--:`); columns beyond the declared set
                // default to left.
                var align = colIndex < columnCount
                    ? table.ColumnDefinitions[colIndex].Alignment
                    : null;
                var textAlignment = align switch
                {
                    TableColumnAlign.Center => TextAlignment.Center,
                    TableColumnAlign.Right => TextAlignment.Right,
                    _ => TextAlignment.Left
                };

                var cellContent = new StackPanel();
                foreach (var child in cell)
                {
                    var rendered = RenderBlock(child, streaming);
                    if (rendered is not null)
                    {
                        if (rendered is TextBlock tb)
                        {
                            tb.Margin = new Thickness(0);
                            tb.FontSize = 14; // web: table font-size 0.9rem
                            tb.TextAlignment = textAlignment;
                            if (row.IsHeader)
                                tb.FontWeight = FontWeights.SemiBold;
                        }
                        cellContent.Children.Add(rendered);
                    }
                }

                var cellBorder = new Border
                {
                    Child = cellContent,
                    Padding = new Thickness(12, 8, 12, 8),
                    // D3 — the width cap that makes prose cells wrap (the
                    // paragraph TextBlocks are already TextWrapping.Wrap;
                    // they just never saw a finite constraint before).
                    MaxWidth = cellCap,
                    BorderThickness = new Thickness(0, 0,
                        colIndex < totalColumns - 1 ? 1 : 0, rowIndex == 0 ? 1 : 0.5)
                };
                cellBorder.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
                if (row.IsHeader)
                    cellBorder.SetResourceReference(Border.BackgroundProperty, "BgSecondaryBrush");

                Grid.SetRow(cellBorder, rowIndex);
                Grid.SetColumn(cellBorder, colIndex);
                grid.Children.Add(cellBorder);
                colIndex++;
            }
            rowIndex++;
        }

        var outer = new Border
        {
            Child = new ScrollViewer
            {
                Content = grid,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                HorizontalAlignment = HorizontalAlignment.Left
            },
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Margin = new Thickness(0, 12, 0, 12),
            // Auto-fit to content instead of stretching the whole column (web: width auto).
            HorizontalAlignment = HorizontalAlignment.Left
        };
        outer.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
        return outer;
    }

    private FrameworkElement RenderRule()
    {
        var rule = new Border { Height = 1, Margin = new Thickness(0, 20, 0, 20) };
        rule.SetResourceReference(Border.BackgroundProperty, "BorderBrush");
        return rule;
    }

    // ---------- inlines ----------

    private void AddInlines(InlineCollection target, ContainerInline container)
    {
        foreach (var inline in container)
        {
            switch (inline)
            {
                case LiteralInline literal:
                    target.Add(new Run(literal.Content.ToString()));
                    break;

                case EmphasisInline emphasis:
                    var span = new Span();
                    if (emphasis.DelimiterCount >= 2) span.FontWeight = FontWeights.Bold;
                    if (emphasis.DelimiterCount is 1 or 3) span.FontStyle = FontStyles.Italic;
                    if (emphasis.DelimiterChar == '~') // strikethrough (GFM)
                        span.TextDecorations = TextDecorations.Strikethrough;
                    AddInlines(span.Inlines, emphasis);
                    target.Add(span);
                    break;

                case CodeInline code:
                    var codeRun = new Run(code.Content)
                    {
                        FontFamily = MonoFont,
                        FontSize = 14
                    };
                    codeRun.SetResourceReference(TextElement.BackgroundProperty, "BgSecondaryBrush");
                    target.Add(codeRun);
                    break;

                case MathInline math:
                    // Inline math is a styled Run, never a WebView2 — a
                    // message can carry dozens of $x$ formulas (see MathTex).
                    target.Add(new Run(MathTex.ToInlineText(math.Content.ToString()))
                    {
                        FontFamily = MathFont,
                        FontStyle = FontStyles.Italic
                    });
                    break;

                case LinkInline { IsImage: true } image:
                    var alt = LabelOf(image) ?? image.Url ?? "image";
                    var inlineImage = TryBuildInlineImage(image, alt);
                    if (inlineImage is not null)
                    {
                        // Phase C PART 1: render the actual image (charts, generated
                        // images) inline instead of an icon + link.
                        target.Add(new InlineUIContainer(inlineImage)
                        {
                            BaselineAlignment = BaselineAlignment.Bottom
                        });
                    }
                    else
                    {
                        // file://, local paths, malformed data URIs → icon + link.
                        var imageIcon = new System.Windows.Shapes.Path
                        {
                            Data = ImageGeometry,
                            Stretch = Stretch.Uniform,
                            Width = 14,
                            Height = 14,
                            StrokeThickness = 1.5,
                            StrokeStartLineCap = PenLineCap.Round,
                            StrokeEndLineCap = PenLineCap.Round,
                            StrokeLineJoin = PenLineJoin.Round,
                            Margin = new Thickness(0, 0, 4, 0)
                        };
                        imageIcon.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty, "TextSecondaryBrush");
                        target.Add(new InlineUIContainer(imageIcon) { BaselineAlignment = BaselineAlignment.Center });
                        target.Add(BuildHyperlink(image, alt));
                    }
                    break;

                case LinkInline link:
                    target.Add(BuildHyperlink(link, LabelOf(link)));
                    break;

                case AutolinkInline autolink:
                    var auto = new Hyperlink(new Run(autolink.Url));
                    WireLink(auto, autolink.Url);
                    target.Add(auto);
                    break;

                case LineBreakInline lineBreak:
                    if (lineBreak.IsHard)
                        target.Add(new LineBreak());
                    else
                        target.Add(new Run(" "));
                    break;

                case TaskList task:
                    target.Add(new Run(task.Checked ? "☑ " : "☐ "));
                    break;

                case HtmlEntityInline entity:
                    target.Add(new Run(entity.Transcoded.ToString()));
                    break;

                case HtmlInline html:
                    target.Add(new Run(html.Tag));
                    break;

                case ContainerInline nested:
                    AddInlines(target, nested);
                    break;

                default:
                    target.Add(new Run(inline.ToString() ?? string.Empty));
                    break;
            }
        }
    }

    /// <summary>Max on-screen size for an inline image — fits the message column
    /// and constrains decode memory.</summary>
    private const int InlineImageMaxDip = 480;
    private const int InlineImageDecodeWidth = 960;

    /// <summary>
    /// Box reserved for a REMOTE image, whose real size is unknowable until the
    /// download finishes — nothing in the message model carries image
    /// dimensions, and asking the network for them would just move the delay.
    /// 16:9 at the column's max width. The image letterboxes inside it
    /// (Stretch=Uniform), so the row is the same height before and after the
    /// bytes arrive. A fixed box that never moves beats an accurate one that
    /// arrives after layout.
    /// </summary>
    private const int RemoteImageReservedWidth = 480;
    private const int RemoteImageReservedHeight = 270;

    /// <summary>
    /// Builds a WPF element rendering an inline markdown image (Phase C PART 1),
    /// or null to fall back to the icon+link. Only data:/http(s) are loaded
    /// (never local files). On decode/load failure it swaps to the alt-text link
    /// so a broken image never leaves a blank gap.
    /// </summary>
    private UIElement? TryBuildInlineImage(LinkInline image, string alt)
    {
        var plan = Rendering.InlineImageResolver.Plan(image.Url);
        var bitmap = TryCreateBitmap(plan);
        if (bitmap is null)
            return null;

        var element = new System.Windows.Controls.Image
        {
            Source = bitmap,
            Stretch = Stretch.Uniform,
            StretchDirection = StretchDirection.DownOnly, // never upscale small charts
            MaxWidth = InlineImageMaxDip,
            MaxHeight = InlineImageMaxDip,
            HorizontalAlignment = HorizontalAlignment.Left,
            Margin = new Thickness(0, 4, 0, 4)
        };
        ReserveInlineImageSize(element, bitmap, plan.Kind);
        System.Windows.Automation.AutomationProperties.SetName(element, alt);
        element.ToolTip = alt;

        // Fallback: if a remote image fails to load, show the alt-text link.
        var fallback = new TextBlock { Visibility = Visibility.Collapsed };
        fallback.Inlines.Add(BuildHyperlink(image, alt));

        var grid = new Grid();
        grid.Children.Add(element);
        grid.Children.Add(fallback);
        element.ImageFailed += (_, _) =>
        {
            element.Visibility = Visibility.Collapsed;
            fallback.Visibility = Visibility.Visible;
        };
        return grid;
    }

    /// <summary>
    /// Give the Image an explicit size BEFORE it is measured, so the row's
    /// height never changes underneath the reader.
    ///
    /// Without this an image contributed nothing to the first measure and the
    /// row grew once the bitmap was ready — which is one of the ways the thread
    /// shifted while scrolling.
    ///
    /// A data: URI is already decoded here (BitmapCacheOption.OnLoad, no
    /// DelayCreation), so its final on-screen size is known exactly and is
    /// reserved exactly. A remote image is not: it is created with
    /// DelayCreation precisely so a slow fetch cannot block the UI, and reading
    /// its dimensions now would either block or lie. It gets the fixed box.
    /// </summary>
    internal static void ReserveInlineImageSize(
        System.Windows.Controls.Image element,
        System.Windows.Media.Imaging.BitmapSource bitmap,
        Rendering.InlineImageKind kind)
    {
        if (kind == Rendering.InlineImageKind.DataUri && bitmap.Width > 0 && bitmap.Height > 0)
        {
            // Same arithmetic Stretch=Uniform + StretchDirection=DownOnly would
            // have produced, applied up front instead of after the fact.
            var scale = Math.Min(1.0, Math.Min(
                InlineImageMaxDip / bitmap.Width,
                InlineImageMaxDip / bitmap.Height));
            element.Width = Math.Round(bitmap.Width * scale);
            element.Height = Math.Round(bitmap.Height * scale);
            return;
        }

        element.Width = RemoteImageReservedWidth;
        element.Height = RemoteImageReservedHeight;
    }

    private static System.Windows.Media.Imaging.BitmapImage? TryCreateBitmap(
        Rendering.InlineImagePlan plan)
    {
        try
        {
            var bmp = new System.Windows.Media.Imaging.BitmapImage();
            bmp.BeginInit();
            bmp.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
            bmp.DecodePixelWidth = InlineImageDecodeWidth; // cap decoded size (perf/memory)
            switch (plan.Kind)
            {
                case Rendering.InlineImageKind.DataUri when plan.Data is { Length: > 0 }:
                    bmp.StreamSource = new System.IO.MemoryStream(plan.Data);
                    break;
                case Rendering.InlineImageKind.Remote when plan.RemoteUrl is not null:
                    bmp.UriSource = new Uri(plan.RemoteUrl, UriKind.Absolute);
                    // Async so a slow/failed http fetch never blocks the UI.
                    bmp.CreateOptions = System.Windows.Media.Imaging.BitmapCreateOptions.DelayCreation;
                    break;
                default:
                    return null;
            }
            bmp.EndInit();
            if (bmp.CanFreeze)
                bmp.Freeze();
            return bmp;
        }
        catch (Exception ex) when (ex is NotSupportedException or System.IO.FileFormatException
            or UriFormatException or ArgumentException or System.IO.IOException)
        {
            // Undecodable payload / bad URI → fall back to the icon+link.
            return null;
        }
    }

    private Hyperlink BuildHyperlink(LinkInline link, string? label)
    {
        var hyperlink = new Hyperlink();
        if (link.Any())
            AddInlines(hyperlink.Inlines, link);
        else
            hyperlink.Inlines.Add(new Run(label ?? link.Url ?? "link"));
        WireLink(hyperlink, link.Url);
        return hyperlink;
    }

    private static string? LabelOf(LinkInline link) =>
        link.FirstChild is LiteralInline literal ? literal.Content.ToString() : null;

    private static void WireLink(Hyperlink hyperlink, string? url)
    {
        hyperlink.SetResourceReference(TextElement.ForegroundProperty, "AccentBrush");
        if (Uri.TryCreate(url, UriKind.Absolute, out var uri)
            && uri.Scheme is "http" or "https" or "mailto")
        {
            hyperlink.ToolTip = url;
            hyperlink.Click += (_, _) =>
            {
                try
                {
                    Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
                }
                catch (Exception)
                {
                    // Browser launch failure is non-fatal.
                }
            };
        }
    }

    private static TextBlock NewTextBlock()
    {
        // Web assistant prose: 16px / line-height 28px (1.75).
        var text = new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            FontSize = 16,
            LineHeight = 28,
            LineStackingStrategy = LineStackingStrategy.BlockLineHeight
        };
        text.SetResourceReference(TextBlock.ForegroundProperty, "TextPrimaryBrush");
        return text;
    }

    private static TextBlock PlainText(string content)
    {
        var text = NewTextBlock();
        text.Text = content;
        return text;
    }
}
