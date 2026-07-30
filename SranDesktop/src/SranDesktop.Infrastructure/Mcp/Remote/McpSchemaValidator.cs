using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

public sealed record McpSchemaVerdict(bool Accepted, string? RejectionCode, string? Reason)
{
    public static McpSchemaVerdict Ok { get; } = new(true, null, null);

    public static McpSchemaVerdict Reject(string code, string reason) =>
        new(false, code, reason);
}

/// <summary>
/// Gate for tool schemas advertised by an MCP server.
///
/// The rule that matters: a schema that does not parse, or that does not
/// constrain anything, is REJECTED — the tool disappears. It is never quietly
/// replaced with a permissive <c>{"type":"object"}</c>, because that turns a
/// broken contract into "this server may send any payload it likes", which is
/// exactly the shape an untrusted server would want.
/// </summary>
public static class McpSchemaValidator
{
    public const int MaxSchemaDepth = 12;
    public const int MaxSchemaBytes = 128 * 1024;
    public const int MaxProperties = 200;

    private static readonly HashSet<string> KnownTypes = new(StringComparer.Ordinal)
    {
        "object", "array", "string", "number", "integer", "boolean", "null"
    };

    public static McpSchemaVerdict Validate(string? rawSchema)
    {
        if (string.IsNullOrWhiteSpace(rawSchema))
            return McpSchemaVerdict.Reject(
                "SchemaMissing",
                "The server advertised a tool without an input schema.");
        if (System.Text.Encoding.UTF8.GetByteCount(rawSchema) > MaxSchemaBytes)
            return McpSchemaVerdict.Reject(
                "SchemaTooLarge",
                $"The schema exceeds the {MaxSchemaBytes}-byte limit.");

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(rawSchema);
        }
        catch (JsonException ex)
        {
            return McpSchemaVerdict.Reject("SchemaUnparseable", ex.Message);
        }

        using (document)
        {
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                return McpSchemaVerdict.Reject(
                    "SchemaNotAnObject", "A tool input schema must be a JSON object.");
            if (!root.TryGetProperty("type", out var type)
                || type.ValueKind != JsonValueKind.String
                || type.GetString() != "object")
                return McpSchemaVerdict.Reject(
                    "SchemaRootNotObjectType",
                    "A tool input schema must declare \"type\": \"object\".");
            // A zero-argument tool legitimately advertises {"type":"object"} with
            // no properties, so their absence is NOT a rejection — the caller
            // simply has no arguments to send.
            if (root.TryGetProperty("properties", out var properties)
                && properties.ValueKind != JsonValueKind.Object)
                return McpSchemaVerdict.Reject(
                    "SchemaPropertiesNotAnObject",
                    "'properties' must be an object mapping names to schemas.");

            // An explicit "anything goes" escape hatch defeats the point of
            // validating the payload at all.
            if (root.TryGetProperty("additionalProperties", out var additional)
                && additional.ValueKind == JsonValueKind.True)
                return McpSchemaVerdict.Reject(
                    "SchemaAllowsArbitraryFields",
                    "additionalProperties:true lets a server send unvalidated fields.");

            return Walk(root, depth: 0);
        }
    }

    private static McpSchemaVerdict Walk(JsonElement element, int depth)
    {
        if (depth > MaxSchemaDepth)
            return McpSchemaVerdict.Reject(
                "SchemaTooDeep",
                $"The schema nests deeper than {MaxSchemaDepth} levels.");

        if (element.TryGetProperty("type", out var type))
        {
            if (type.ValueKind == JsonValueKind.String
                && !KnownTypes.Contains(type.GetString() ?? ""))
                return McpSchemaVerdict.Reject(
                    "SchemaUnknownType", $"'{type.GetString()}' is not a JSON Schema type.");
            if (type.ValueKind == JsonValueKind.Array)
            {
                foreach (var candidate in type.EnumerateArray())
                {
                    if (candidate.ValueKind != JsonValueKind.String
                        || !KnownTypes.Contains(candidate.GetString() ?? ""))
                        return McpSchemaVerdict.Reject(
                            "SchemaUnknownType",
                            "A union type contains an unrecognized JSON Schema type.");
                }
            }
        }

        if (element.TryGetProperty("enum", out var enumeration))
        {
            if (enumeration.ValueKind != JsonValueKind.Array
                || enumeration.GetArrayLength() == 0)
                return McpSchemaVerdict.Reject(
                    "SchemaInvalidEnum", "An enum must be a non-empty array.");
        }

        if (element.TryGetProperty("required", out var required))
        {
            if (required.ValueKind != JsonValueKind.Array)
                return McpSchemaVerdict.Reject(
                    "SchemaInvalidRequired", "'required' must be an array of property names.");
            var declared = element.TryGetProperty("properties", out var declaredProperties)
                           && declaredProperties.ValueKind == JsonValueKind.Object
                ? declaredProperties.EnumerateObject().Select(item => item.Name).ToHashSet(
                    StringComparer.Ordinal)
                : [];
            foreach (var name in required.EnumerateArray())
            {
                if (name.ValueKind != JsonValueKind.String)
                    return McpSchemaVerdict.Reject(
                        "SchemaInvalidRequired", "'required' entries must be strings.");
                if (!declared.Contains(name.GetString() ?? ""))
                    // A required field with no definition cannot be checked.
                    return McpSchemaVerdict.Reject(
                        "SchemaRequiredFieldUndeclared",
                        $"'{name.GetString()}' is required but never declared.");
            }
        }

        if (element.TryGetProperty("properties", out var properties)
            && properties.ValueKind == JsonValueKind.Object)
        {
            var count = 0;
            foreach (var property in properties.EnumerateObject())
            {
                if (++count > MaxProperties)
                    return McpSchemaVerdict.Reject(
                        "SchemaTooManyProperties",
                        $"A schema may declare at most {MaxProperties} properties.");
                if (property.Value.ValueKind != JsonValueKind.Object)
                    return McpSchemaVerdict.Reject(
                        "SchemaPropertyNotAnObject",
                        $"Property '{property.Name}' is not described by a schema object.");
                var nested = Walk(property.Value, depth + 1);
                if (!nested.Accepted)
                    return nested;
            }
        }

        if (element.TryGetProperty("items", out var items)
            && items.ValueKind == JsonValueKind.Object)
        {
            var nested = Walk(items, depth + 1);
            if (!nested.Accepted)
                return nested;
        }

        return McpSchemaVerdict.Ok;
    }

    /// <summary>
    /// Qualifies a tool name with its connector so two servers cannot both claim
    /// "search" and silently shadow one another.
    /// </summary>
    public static string Namespaced(string connectorId, string toolName) =>
        $"{connectorId}.{toolName}";

    public static bool IsValidToolName(string? toolName) =>
        toolName is { Length: > 0 and <= 128 }
        && toolName.All(character =>
            char.IsAsciiLetterOrDigit(character) || character is '_' or '-');
}
