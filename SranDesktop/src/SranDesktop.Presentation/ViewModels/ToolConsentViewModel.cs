using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Read-only view state for the tool consent dialog.</summary>
public sealed class ToolConsentViewModel
{
    private static readonly HashSet<string> DangerousTools =
        new(StringComparer.OrdinalIgnoreCase) { "write_file", "run_command", "write_clipboard" };

    public string ToolName { get; }
    public string Description { get; }
    public string ArgumentsFormatted { get; }
    public ToolCategory Category { get; }
    public bool IsDangerous { get; }

    public ToolConsentViewModel(ToolConsentRequest request)
    {
        ToolName = request.ToolName;
        Description = request.Description;
        ArgumentsFormatted = PrettyPrint(request.ArgumentsJson);
        Category = request.Category;
        IsDangerous = DangerousTools.Contains(request.ToolName)
                      || request.Category == ToolCategory.Shell;
    }

    private static string PrettyPrint(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            return JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true });
        }
        catch (JsonException)
        {
            return json;
        }
    }
}
