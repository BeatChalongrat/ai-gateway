using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

public sealed record DataEgressDecision(
    bool Allowed,
    bool RequiresConsent,
    string ReasonCode,
    string? Destination,
    bool ExternalTransmission,
    SensitiveDataClassification Sensitivity);

/// <summary>
/// Deterministic policy evaluated after model tool selection and before tool
/// dispatch. Model-produced text and external observations cannot weaken it.
/// </summary>
public static class DataEgressPolicy
{
    public static DataEgressDecision Evaluate(
        IMcpTool tool,
        string argumentsJson,
        SideEffectClass sideEffect,
        ToolDataFlowPolicy? declaredFlow,
        IEnumerable<GatewayMessage> messages)
    {
        var sensitivity = SensitiveDataRedactor.Classify(argumentsJson);
        var hasUntrustedInput =
            UntrustedObservationBoundary.ContainsUntrustedObservation(messages);
        var external = declaredFlow?.ExternalTransmission == true
                       || tool.Category is ToolCategory.Network or ToolCategory.Browser;
        var destination = declaredFlow?.Destination
                          ?? (external ? DestinationFor(tool, argumentsJson) : null);

        if (external && string.IsNullOrWhiteSpace(destination))
            return new DataEgressDecision(
                false, false, "UnknownExternalDestination", null, true, sensitivity);

        if (hasUntrustedInput && sideEffect != SideEffectClass.ReadOnly)
            return new DataEgressDecision(
                true, true, "UntrustedContentCannotAuthorizeSideEffect",
                destination, external, sensitivity);

        if (hasUntrustedInput && external)
            return new DataEgressDecision(
                true, true, "UntrustedContentSelectedExternalDestination",
                destination, true, sensitivity);

        if (external && sensitivity is SensitiveDataClassification.Secret
            or SensitiveDataClassification.Confidential
            or SensitiveDataClassification.Personal)
            return new DataEgressDecision(
                true, true, "SensitiveExternalTransmission",
                destination, true, sensitivity);

        return new DataEgressDecision(
            true,
            declaredFlow?.AlwaysRequireConfirmation == true,
            declaredFlow?.AlwaysRequireConfirmation == true
                ? "DeclaredSensitiveDataFlow"
                : "PolicySatisfied",
            destination,
            external,
            sensitivity);
    }

    private static string? DestinationFor(IMcpTool tool, string argumentsJson)
    {
        if (tool.Name is "open_url" or "browser_navigate" or "web_fetch")
            return HttpOrigin(argumentsJson);
        if (tool.Category == ToolCategory.Browser)
            return "active browser origin";
        if (tool.Name.StartsWith("mcp_", StringComparison.Ordinal))
            return "configured MCP connector";
        return tool.Name switch
        {
            "web_search" or "web_fetch" => "configured web service",
            "run_python" => "configured code sandbox service",
            _ when tool.Category == ToolCategory.Network => "configured network service",
            _ => null
        };
    }

    private static string? HttpOrigin(string argumentsJson)
    {
        try
        {
            using var document = System.Text.Json.JsonDocument.Parse(argumentsJson);
            if (document.RootElement.ValueKind != System.Text.Json.JsonValueKind.Object
                || !document.RootElement.TryGetProperty("url", out var urlElement)
                || urlElement.ValueKind != System.Text.Json.JsonValueKind.String
                || !Uri.TryCreate(urlElement.GetString(), UriKind.Absolute, out var uri)
                || uri.Scheme is not ("http" or "https"))
                return null;
            return uri.GetComponents(
                UriComponents.SchemeAndServer,
                UriFormat.SafeUnescaped);
        }
        catch (System.Text.Json.JsonException)
        {
            return null;
        }
    }
}
