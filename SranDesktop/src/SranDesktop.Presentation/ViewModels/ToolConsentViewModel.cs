using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Read-only view state for the tool consent dialog.</summary>
public sealed class ToolConsentViewModel
{
    public string ToolName { get; }
    public string Description { get; }
    public string ArgumentsFormatted { get; }
    public ToolCategory Category { get; }

    /// <summary>True when the action can affect the machine or the outside world —
    /// drives the prominent danger banner (Phase C PART 3, capability-derived).</summary>
    public bool IsDangerous { get; }

    /// <summary>A short, localized "why" for the banner (empty when not dangerous).</summary>
    public string DangerReason { get; }

    public ToolConsentViewModel(ToolConsentRequest request)
    {
        ToolName = request.ToolName;
        Description = request.Description;
        ArgumentsFormatted = PrettyPrint(request.ArgumentsJson);
        Category = request.Category;

        var danger = ToolDangerClassifier.Classify(request.ToolName, request.Category);
        IsDangerous = danger.IsDangerous;
        DangerReason = danger.IsDangerous
            ? Localization.LocaleService.Instance.T(danger.ReasonKey)
            : string.Empty;
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
