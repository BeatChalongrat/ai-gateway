using System.Text.Json;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using SranDesktop.Infrastructure.Office;
using P = DocumentFormat.OpenXml.Presentation;

namespace SranDesktop.DocumentWorker;

internal static class PptxOperations
{
    private const long DefaultWidth = 12192000;  // 16:9 at 13.333in
    private const long DefaultHeight = 6858000;

    public static void Create(string path, JsonElement payload)
    {
        using var document = PresentationDocument.Create(
            path, PresentationDocumentType.Presentation);
        var presentationPart = document.AddPresentationPart();
        var (layoutPart, slideIds) = PptxBackbone.Build(
            presentationPart, DefaultWidth, DefaultHeight);

        var slides = payload.TryGetProperty("slides", out var element)
                     && element.ValueKind == JsonValueKind.Array
            ? element.EnumerateArray().ToList()
            : [];
        var index = 0;
        foreach (var slide in slides)
            AddSlide(presentationPart, layoutPart, slideIds, slide, index++);

        if (index == 0)
            // PowerPoint refuses to open a deck with an empty slide list.
            AddSlide(
                presentationPart,
                layoutPart,
                slideIds,
                JsonDocument.Parse("{}").RootElement,
                0);
        presentationPart.Presentation!.Save();
    }

    public static void Edit(string path, JsonElement payload)
    {
        using var document = PresentationDocument.Open(path, isEditable: true);
        var presentationPart = document.PresentationPart
                               ?? throw new InvalidOperationException(
                                   "The presentation has no presentation part.");
        var slideIds = presentationPart.Presentation?.GetFirstChild<P.SlideIdList>()
                       ?? throw new InvalidOperationException(
                           "The presentation has no slide list.");

        if (payload.TryGetProperty("edits", out var edits)
            && edits.ValueKind == JsonValueKind.Array)
        {
            foreach (var edit in edits.EnumerateArray())
                ApplyEdit(presentationPart, slideIds, edit);
        }
        presentationPart.Presentation!.Save();
    }

    private static void ApplyEdit(
        PresentationPart presentationPart,
        P.SlideIdList slideIds,
        JsonElement edit)
    {
        var operation = Str(edit, "op");
        switch (operation)
        {
            case "add_slide":
            {
                var layoutPart = presentationPart.SlideMasterParts
                                     .FirstOrDefault()?.SlideLayoutParts.FirstOrDefault()
                                 ?? throw new InvalidOperationException(
                                     "The deck has no slide layout to attach a new slide to.");
                AddSlide(
                    presentationPart,
                    layoutPart,
                    slideIds,
                    edit,
                    slideIds.ChildElements.Count);
                break;
            }

            case "delete_slide":
            {
                var index = Int(edit, "index") ?? throw new InvalidOperationException(
                    "delete_slide requires 'index'.");
                var ids = slideIds.Elements<P.SlideId>().ToList();
                if (index < 0 || index >= ids.Count)
                    throw new InvalidOperationException(
                        $"delete_slide index {index} is outside the deck ({ids.Count} slides).");
                if (ids.Count == 1)
                    throw new InvalidOperationException(
                        "Deleting the only slide would produce an unopenable deck.");
                var relationshipId = ids[index].RelationshipId!.Value!;
                var slidePart = (SlidePart)presentationPart.GetPartById(relationshipId);
                ids[index].Remove();
                presentationPart.DeletePart(slidePart);
                break;
            }

            case "set_slide_text":
            {
                var index = Int(edit, "index") ?? throw new InvalidOperationException(
                    "set_slide_text requires 'index'.");
                var ids = slideIds.Elements<P.SlideId>().ToList();
                if (index < 0 || index >= ids.Count)
                    throw new InvalidOperationException(
                        $"set_slide_text index {index} is outside the deck.");
                var slidePart = (SlidePart)presentationPart.GetPartById(
                    ids[index].RelationshipId!.Value!);
                var texts = slidePart.Slide.Descendants<
                    DocumentFormat.OpenXml.Drawing.Text>().ToList();
                var target = Int(edit, "shape") ?? 0;
                if (target < 0 || target >= texts.Count)
                    throw new InvalidOperationException(
                        $"set_slide_text shape {target} does not exist on slide {index}.");
                texts[target].Text = Str(edit, "text") ?? "";
                slidePart.Slide.Save();
                break;
            }

            default:
                throw new InvalidOperationException(
                    $"'{operation}' is not a supported PPTX edit.");
        }
    }

    private static void AddSlide(
        PresentationPart presentationPart,
        SlideLayoutPart layoutPart,
        P.SlideIdList slideIds,
        JsonElement slide,
        int index)
    {
        var tree = new ShapeTreeBuilder();
        var title = Str(slide, "title");
        var body = Str(slide, "body");
        if (!string.IsNullOrEmpty(title))
            tree.TextBox(0.8, 0.7, 11.7, 1.4, title, "Calibri", 34, bold: true, "0A1230");
        if (!string.IsNullOrEmpty(body))
            tree.TextBox(0.8, 2.3, 11.7, 4.0, body, "Calibri", 18, bold: false, "1F2937");
        if (slide.TryGetProperty("bullets", out var bullets)
            && bullets.ValueKind == JsonValueKind.Array)
        {
            var items = bullets.EnumerateArray()
                .Select(item => item.ValueKind == JsonValueKind.String
                    ? item.GetString() ?? ""
                    : item.ToString())
                .Where(item => item.Length > 0)
                .ToList();
            if (items.Count > 0)
                tree.Bullets(0.8, 2.3, 11.7, 4.0, items, "Calibri", 18, "1F2937");
        }

        var slidePart = presentationPart.AddNewPart<SlidePart>();
        slidePart.Slide = tree.ToSlide();
        slidePart.AddPart(layoutPart, "rId1");
        slideIds.Append(new P.SlideId
        {
            Id = 256U + (uint)index,
            RelationshipId = presentationPart.GetIdOfPart(slidePart)
        });
    }

    private static string? Str(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static int? Int(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.TryGetInt32(out var number)
            ? number
            : null;
}
