using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Read-only view state for the tool consent dialog.</summary>
public sealed class ToolConsentViewModel
{
    /// <summary>Longest argument value shown before truncation — the dialog is
    /// a decision point, not a file viewer (FIX-C).</summary>
    private const int MaxValueChars = 200;

    public string ToolName { get; }

    /// <summary>Localized human name (tool.name.*), falling back to the raw
    /// tool id for tools without a dictionary entry (FIX-C).</summary>
    public string DisplayName { get; }

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
        DisplayName = Lookup($"tool.name.{request.ToolName}", request.ToolName);
        Description = request.Description;
        ArgumentsFormatted = FormatArguments(request.ArgumentsJson);
        Category = request.Category;

        var danger = ToolDangerClassifier.Classify(request.ToolName, request.Category);
        IsDangerous = danger.IsDangerous;
        DangerReason = danger.IsDangerous
            ? Localization.LocaleService.Instance.T(danger.ReasonKey)
            : string.Empty;
    }

    /// <summary>T() with an explicit fallback — T() itself falls back to
    /// returning the KEY, which is worse than the raw value here.</summary>
    private static string Lookup(string key, string fallback)
    {
        var localized = Localization.LocaleService.Instance.T(key);
        return localized == key ? fallback : localized;
    }

    /// <summary>"Label: value" lines instead of raw JSON (FIX-C) — the person
    /// deciding must be able to READ what they are allowing. Labels come from
    /// tool.arg.*; long values are truncated with a localized marker.</summary>
    internal static string FormatArguments(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return Truncate(doc.RootElement.ToString());

            var lines = new List<string>();
            foreach (var property in doc.RootElement.EnumerateObject())
            {
                if (property.Value.ValueKind == JsonValueKind.Null)
                    continue;
                var label = Lookup($"tool.arg.{property.Name}", property.Name);
                lines.Add($"{label}: {Truncate(FormatValue(property.Value))}");
            }
            return lines.Count > 0
                ? string.Join(Environment.NewLine, lines)
                : "—";
        }
        catch (JsonException)
        {
            return Truncate(json);
        }
    }

    private static string FormatValue(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.True => "✓",
        JsonValueKind.False => "✗",
        // Nested arrays/objects (rare in tool args): compact JSON beats nothing.
        _ => value.GetRawText()
    };

    private static string Truncate(string value)
    {
        value = value.ReplaceLineEndings(" ").Trim();
        if (value.Length <= MaxValueChars)
            return value;
        return value[..MaxValueChars] + " … " + Localization.LocaleService.Instance.T("consent.truncated");
    }
}
