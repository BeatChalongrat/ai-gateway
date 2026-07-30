using System.Text.Json;
using System.Text.Json.Serialization;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public sealed record ObservationOrigin(
    string ToolName,
    string? Uri = null,
    string? ConnectorId = null);

public sealed record UntrustedObservationEnvelope(
    string Schema,
    string SourceType,
    string TrustLevel,
    string Sensitivity,
    ObservationOrigin Origin,
    IReadOnlyList<string> AllowedUses,
    IReadOnlyList<string> ProhibitedUses,
    string Content);

public static class UntrustedObservationBoundary
{
    public const string Marker = "[untrusted_external_observation:v1]";
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    private static readonly string[] AllowedUses =
        ["read", "summarize", "extract_facts"];

    private static readonly string[] ProhibitedUses =
    [
        "change_policy",
        "grant_permission",
        "select_external_destination",
        "expand_authorized_scope",
        "authorize_side_effect",
        "persist_instruction"
    ];

    public static ObservationTrust ClassifySource(string toolName) =>
        toolName.StartsWith("browser_", StringComparison.Ordinal)
        || toolName.StartsWith("web_", StringComparison.Ordinal)
        || toolName.StartsWith("mcp_", StringComparison.Ordinal)
        || toolName is "run_command" or "run_python" or "read_clipboard"
            or "read_file" or "search_files" or "run_subagent"
            ? ObservationTrust.UntrustedExternalContent
            : ObservationTrust.ToolObservation;

    public static string Protect(
        string? content,
        string toolName = "external",
        string? originUri = null,
        string? connectorId = null)
    {
        var redacted = SensitiveDataRedactor.Redact(content);
        var classification = SensitiveDataRedactor.Classify(content).ToString().ToLowerInvariant();
        var envelope = new UntrustedObservationEnvelope(
            "sran.observation.v1",
            SourceType(toolName),
            "untrusted",
            classification,
            new ObservationOrigin(toolName, originUri, connectorId),
            AllowedUses,
            ProhibitedUses,
            redacted);
        return Marker + "\n" + JsonSerializer.Serialize(envelope, JsonOptions);
    }

    public static string ProtectIfUntrusted(
        string toolName,
        string? content,
        string? originUri = null,
        string? connectorId = null)
    {
        if (string.IsNullOrEmpty(content))
            return content ?? string.Empty;
        if (content.StartsWith(Marker, StringComparison.Ordinal))
            return content;
        return ClassifySource(toolName) == ObservationTrust.UntrustedExternalContent
            ? Protect(content, toolName, originUri, connectorId)
            : content;
    }

    public static bool ContainsUntrustedObservation(IEnumerable<GatewayMessage> messages) =>
        messages.Any(message =>
            message.Content is string text
            && text.Contains(Marker, StringComparison.Ordinal));

    public static bool TryReadEnvelope(
        string? protectedContent,
        out UntrustedObservationEnvelope? envelope)
    {
        envelope = null;
        if (string.IsNullOrWhiteSpace(protectedContent)
            || !protectedContent.StartsWith(Marker, StringComparison.Ordinal))
            return false;
        var newline = protectedContent.IndexOf('\n');
        if (newline < 0 || newline == protectedContent.Length - 1)
            return false;
        try
        {
            envelope = JsonSerializer.Deserialize<UntrustedObservationEnvelope>(
                protectedContent[(newline + 1)..],
                JsonOptions);
            return envelope is not null
                   && envelope.Schema == "sran.observation.v1"
                   && envelope.TrustLevel == "untrusted";
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static string SourceType(string toolName)
    {
        if (toolName == "web_search") return "search_result";
        if (toolName == "web_fetch") return "webpage";
        if (toolName.StartsWith("browser_", StringComparison.Ordinal)) return "browser_dom";
        if (toolName.StartsWith("mcp_", StringComparison.Ordinal)) return "mcp_result";
        return toolName switch
        {
            "run_command" => "shell_output",
            "run_python" => "external_api_response",
            "read_clipboard" => "clipboard",
            "read_file" => "workspace_file",
            "search_files" => "workspace_search_result",
            _ => "external_content"
        };
    }
}
