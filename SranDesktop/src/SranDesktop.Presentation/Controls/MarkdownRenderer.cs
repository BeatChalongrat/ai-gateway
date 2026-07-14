using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using Markdig;
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
    private static readonly MarkdownPipeline Pipeline = new MarkdownPipelineBuilder()
        .UseAdvancedExtensions()
        .Build();

    // Match web's JetBrains Mono vibe with fonts that ship on Windows.
    private static readonly FontFamily MonoFont =
        new("Cascadia Code, Cascadia Mono, JetBrains Mono, Consolas, Courier New");

    private static readonly Geometry CopyGeometry = Geometry.Parse(
        "M20 8H10a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2Z " +
        "M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2");

    private static readonly Geometry CheckGeometry = Geometry.Parse("M20 6 9 17l-5-5");

    private static readonly Geometry ImageGeometry = Geometry.Parse(
        "M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2Z " +
        "M8.5 13.5 11 16.51 14.5 12 19 18H5l3.5-4.5Z");

    /// <summary>Parses with the renderer's exact pipeline — the streaming
    /// viewer must diff the SAME block structure this class renders.</summary>
    internal static MarkdownDocument ParseDocument(string markdown) =>
        Markdig.Markdown.Parse(markdown, Pipeline);

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
        ParagraphBlock paragraph => RenderParagraph(paragraph),
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

    private TextBlock RenderParagraph(ParagraphBlock paragraph)
    {
        var text = NewTextBlock();
        text.Margin = new Thickness(0, 0, 0, 16);
        if (paragraph.Inline is not null)
            AddInlines(text.Inlines, paragraph.Inline);
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
            ToolTip = "Copy code"
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

    private FrameworkElement RenderTable(Table table, bool streaming = false)
    {
        var grid = new Grid();
        var columnCount = table.ColumnDefinitions.Count;
        for (var c = 0; c < columnCount; c++)
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var rowIndex = 0;
        foreach (var row in table.OfType<TableRow>())
        {
            grid.RowDefinitions.Add(new RowDefinition());
            var colIndex = 0;
            foreach (var cell in row.OfType<TableCell>())
            {
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
                    BorderThickness = new Thickness(0, 0,
                        colIndex < columnCount - 1 ? 1 : 0, rowIndex == 0 ? 1 : 0.5)
                };
                cellBorder.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
                if (row.IsHeader)
                    cellBorder.SetResourceReference(Border.BackgroundProperty, "BgSecondaryBrush");

                Grid.SetRow(cellBorder, rowIndex);
                Grid.SetColumn(cellBorder, colIndex);
                grid.Children.Add(cellBorder);
                colIndex++;
                if (colIndex >= columnCount) break;
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

                case LinkInline { IsImage: true } image:
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
                    target.Add(BuildHyperlink(image, LabelOf(image) ?? image.Url ?? "image"));
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
