using System.Text.Json;

namespace SranDesktop.Infrastructure.Office;

/// <summary>Small JsonElement accessors shared by the local plan executors.
/// The plan JSON is the AI-emitted fence body — every field is optional and
/// loosely typed, so readers tolerate wrong kinds by returning null.</summary>
internal static class PlanJson
{
    public static string? GetString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    public static bool GetBool(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.True;

    public static int? GetInt(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : null;

    public static double? GetDouble(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble()
            : null;

    public static bool TryGetArray(JsonElement element, string property, out JsonElement array)
    {
        if (element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(property, out array)
            && array.ValueKind == JsonValueKind.Array)
            return true;
        array = default;
        return false;
    }

    public static bool TryGetObject(JsonElement element, string property, out JsonElement obj)
    {
        if (element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(property, out obj)
            && obj.ValueKind == JsonValueKind.Object)
            return true;
        obj = default;
        return false;
    }

    /// <summary>Loose scalar-to-text used for table cells in docx plans.</summary>
    public static string AsDisplayString(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.Null or JsonValueKind.Undefined => "",
        _ => value.GetRawText(),
    };
}
