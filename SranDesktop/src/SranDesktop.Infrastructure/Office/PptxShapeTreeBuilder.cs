using A = DocumentFormat.OpenXml.Drawing;
using P = DocumentFormat.OpenXml.Presentation;

namespace SranDesktop.Infrastructure.Office;

/// <summary>
/// Accumulates the free-form shapes of a single blank-layout slide (text boxes,
/// filled rectangles, tables) and emits a <see cref="P.Slide"/>. Mirrors the
/// direct add_textbox / add_shape / add_table drawing that
/// scripts/pptx_generator.py performs — positions are given in inches and
/// converted to EMU here.
/// </summary>
internal sealed class ShapeTreeBuilder
{
    private const long Emu = 914400; // EMU per inch

    private readonly List<DocumentFormat.OpenXml.OpenXmlElement> _shapes = new();
    private string? _backgroundHex;
    private uint _nextId = 2; // 1 is reserved for the shape-tree group itself

    public void Background(string hex) => _backgroundHex = Normalize(hex);

    public void TextBox(
        double xIn, double yIn, double wIn, double hIn, string text,
        string font, int sizePt, bool bold, string colorHex,
        A.TextAlignmentTypeValues alignment = default)
    {
        _shapes.Add(BuildShape(
            xIn, yIn, wIn, hIn, fillHex: null, isTextBox: true,
            new[] { Paragraph(text, font, sizePt, bold, colorHex, alignment) }));
    }

    public void Bullets(
        double xIn, double yIn, double wIn, double hIn, IReadOnlyList<string> items,
        string font, int sizePt, string colorHex)
    {
        var paragraphs = items
            .Select(item => Paragraph("•  " + item, font, sizePt, false, colorHex, A.TextAlignmentTypeValues.Left))
            .ToArray();
        _shapes.Add(BuildShape(xIn, yIn, wIn, hIn, fillHex: null, isTextBox: true, paragraphs));
    }

    /// <summary>Filled rectangle (card / accent bar) — no outline, like the
    /// server's add_shape(RECTANGLE) with line.fill.background().</summary>
    public void Rectangle(double xIn, double yIn, double wIn, double hIn, string fillHex)
    {
        _shapes.Add(BuildShape(
            xIn, yIn, wIn, hIn, fillHex: Normalize(fillHex), isTextBox: false,
            Array.Empty<A.Paragraph>()));
    }

    public void Table(
        double xIn, double yIn, double wIn, double hIn,
        IReadOnlyList<string> headers, IReadOnlyList<IReadOnlyList<string>> rows,
        int numCols, string bodyFont,
        string primaryHex, string lightBgHex, string textDarkHex, string textLightHex)
    {
        var cx = (long)(wIn * Emu);
        var cy = (long)(hIn * Emu);
        var colWidth = cx / Math.Max(numCols, 1);

        var grid = new A.TableGrid();
        for (var i = 0; i < numCols; i++)
            grid.Append(new A.GridColumn { Width = colWidth });

        var totalRows = (headers.Count > 0 ? 1 : 0) + rows.Count;
        var rowHeight = cy / Math.Max(totalRows, 1);

        var table = new A.Table(new A.TableProperties { FirstRow = true, BandRow = true }, grid);

        if (headers.Count > 0)
        {
            var headerRow = new A.TableRow { Height = rowHeight };
            foreach (var header in headers)
                headerRow.Append(Cell(header, bodyFont, 13, bold: true,
                    textHex: textLightHex, fillHex: primaryHex, A.TextAlignmentTypeValues.Center));
            table.Append(headerRow);
        }

        for (var i = 0; i < rows.Count; i++)
        {
            var row = new A.TableRow { Height = rowHeight };
            var fill = i % 2 == 0 ? "FFFFFF" : lightBgHex;
            for (var j = 0; j < numCols; j++)
            {
                var value = j < rows[i].Count ? rows[i][j] : "";
                row.Append(Cell(value, bodyFont, 12, bold: false,
                    textHex: textDarkHex, fillHex: fill, A.TextAlignmentTypeValues.Left));
            }
            table.Append(row);
        }

        var frame = new P.GraphicFrame(
            new P.NonVisualGraphicFrameProperties(
                new P.NonVisualDrawingProperties { Id = _nextId++, Name = "Table" },
                new P.NonVisualGraphicFrameDrawingProperties(),
                new P.ApplicationNonVisualDrawingProperties()),
            new P.Transform(
                new A.Offset { X = (long)(xIn * Emu), Y = (long)(yIn * Emu) },
                new A.Extents { Cx = cx, Cy = cy }),
            new A.Graphic(new A.GraphicData(table)
            {
                Uri = "http://schemas.openxmlformats.org/drawingml/2006/table",
            }));
        _shapes.Add(frame);
    }

    public P.Slide ToSlide()
    {
        var common = new P.CommonSlideData();
        if (_backgroundHex is not null)
            common.Append(new P.Background(new P.BackgroundProperties(
                new A.SolidFill(new A.RgbColorModelHex { Val = _backgroundHex }),
                new A.EffectList())));

        var tree = new P.ShapeTree(
            new P.NonVisualGroupShapeProperties(
                new P.NonVisualDrawingProperties { Id = 1U, Name = "" },
                new P.NonVisualGroupShapeDrawingProperties(),
                new P.ApplicationNonVisualDrawingProperties()),
            new P.GroupShapeProperties(new A.TransformGroup(
                new A.Offset { X = 0L, Y = 0L },
                new A.Extents { Cx = 0L, Cy = 0L },
                new A.ChildOffset { X = 0L, Y = 0L },
                new A.ChildExtents { Cx = 0L, Cy = 0L })));
        foreach (var shape in _shapes)
            tree.Append(shape);
        common.Append(tree);

        return new P.Slide(common, new P.ColorMapOverride(new A.MasterColorMapping()));
    }

    // ── Element factories ───────────────────────────────────────────────────

    private P.Shape BuildShape(
        double xIn, double yIn, double wIn, double hIn,
        string? fillHex, bool isTextBox, IReadOnlyList<A.Paragraph> paragraphs)
    {
        var shapeProps = new P.ShapeProperties(
            new A.Transform2D(
                new A.Offset { X = (long)(xIn * Emu), Y = (long)(yIn * Emu) },
                new A.Extents { Cx = (long)(wIn * Emu), Cy = (long)(hIn * Emu) }),
            new A.PresetGeometry(new A.AdjustValueList()) { Preset = A.ShapeTypeValues.Rectangle });
        if (fillHex is not null)
        {
            shapeProps.Append(new A.SolidFill(new A.RgbColorModelHex { Val = fillHex }));
            shapeProps.Append(new A.Outline(new A.NoFill()));
        }

        var body = new P.TextBody(new A.BodyProperties { Wrap = A.TextWrappingValues.Square }, new A.ListStyle());
        if (paragraphs.Count == 0)
            body.Append(new A.Paragraph()); // shapes still need at least one paragraph
        else
            foreach (var p in paragraphs)
                body.Append(p);

        return new P.Shape(
            new P.NonVisualShapeProperties(
                new P.NonVisualDrawingProperties { Id = _nextId++, Name = "Shape" },
                new P.NonVisualShapeDrawingProperties { TextBox = isTextBox },
                new P.ApplicationNonVisualDrawingProperties()),
            shapeProps,
            body);
    }

    private static A.Paragraph Paragraph(
        string text, string font, int sizePt, bool bold, string colorHex,
        A.TextAlignmentTypeValues alignment)
    {
        var props = new A.ParagraphProperties();
        if (alignment != default)
            props.Alignment = alignment;

        var run = new A.Run(
            new A.RunProperties(
                new A.SolidFill(new A.RgbColorModelHex { Val = Normalize(colorHex) }),
                new A.LatinFont { Typeface = font })
            {
                Language = "en-US",
                FontSize = sizePt * 100,
                Bold = bold,
                Dirty = false,
            },
            new A.Text(text));
        return new A.Paragraph(props, run);
    }

    private A.TableCell Cell(
        string text, string font, int sizePt, bool bold, string textHex, string fillHex,
        A.TextAlignmentTypeValues alignment)
    {
        var body = new A.TextBody(
            new A.BodyProperties(),
            new A.ListStyle(),
            Paragraph(text, font, sizePt, bold, textHex, alignment));
        var props = new A.TableCellProperties(
            new A.SolidFill(new A.RgbColorModelHex { Val = Normalize(fillHex) }));
        return new A.TableCell(body, props);
    }

    /// <summary>Plans give "0A1230" or "#0A1230" (occasionally 8-digit ARGB);
    /// normalise to the 6-hex RRGGBB the DrawingML srgbClr wants.</summary>
    private static string Normalize(string hex)
    {
        var h = hex.TrimStart('#');
        if (h.Length == 8)
            h = h[2..];
        return h.ToUpperInvariant();
    }
}
