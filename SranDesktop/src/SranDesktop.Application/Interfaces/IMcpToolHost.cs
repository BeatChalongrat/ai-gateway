using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Interfaces;

/// <summary>A locally-executed desktop tool exposed to the model.</summary>
public interface IMcpTool
{
    string Name { get; }
    string Description { get; }
    JsonElement ParametersSchema { get; }
    ToolCategory Category { get; }
    PermissionLevel DefaultPermission { get; }

    ToolCapabilityDescriptor DescribeCapability() => new(
        Name,
        Category,
        ReadOnly: DefaultPermission == PermissionLevel.AllowAlways,
        Reversible: Category is ToolCategory.FileSystem or ToolCategory.Clipboard,
        Idempotent: Category is ToolCategory.FileSystem,
        ExternalSideEffect: Category is ToolCategory.Network or ToolCategory.Browser,
        LocalSideEffect: Category is ToolCategory.FileSystem or ToolCategory.Shell
            or ToolCategory.Screen or ToolCategory.Clipboard,
        RequiresNetwork: Category is ToolCategory.Network or ToolCategory.Browser,
        RequiresWorkspaceWrite: Category is ToolCategory.FileSystem or ToolCategory.Shell,
        RequiresUserConfirmation: DefaultPermission == PermissionLevel.Ask);

    ToolRequestValidation ValidateRequest(JsonElement arguments) =>
        arguments.ValueKind == JsonValueKind.Object
            ? ToolRequestValidation.Accepted
            : new ToolRequestValidation(
                false,
                "InvalidArgument",
                "Tool arguments must be a JSON object.");

    Task<PreparedToolRequest> PrepareAsync(
        JsonElement arguments,
        CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        return Task.FromResult(new PreparedToolRequest(
            SensitiveDataRedactor.Fingerprint(arguments.GetRawText()),
            DateTimeOffset.UtcNow));
    }

    Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct);

    Task<StructuredToolObservation> ObserveAsync(
        ToolResult result,
        CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var content = result.Success ? result.Content : result.Error ?? string.Empty;
        return Task.FromResult(new StructuredToolObservation(
            Name,
            UntrustedObservationBoundary.ClassifySource(Name),
            SensitiveDataRedactor.Classify(content),
            SensitiveDataRedactor.Fingerprint(content),
            System.Text.Encoding.UTF8.GetByteCount(content),
            ["read", "summarize", "extract_facts", "verify"],
            ["change_policy", "grant_permission", "select_external_destination"]));
    }

    Task<ToolContractVerification> VerifyAsync(
        ToolResult result,
        StructuredToolObservation observation,
        CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var verified = result.Effect?.Verified == true
                       || result.ComputerAction?.EffectVerified == true;
        return Task.FromResult(new ToolContractVerification(
            verified
                ? VerificationDisposition.Verified
                : result.Success
                    ? VerificationDisposition.Inconclusive
                    : VerificationDisposition.Failed,
            result.Effect?.Evidence
            ?? result.ComputerAction?.Evidence
            ?? Array.Empty<string>(),
            verified
                ? "The tool returned independently verifiable evidence."
                : "The tool result does not independently prove the expected effect."));
    }

    Task<ToolReconciliationResult> ReconcileAsync(
        StructuredToolObservation observation,
        CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        return Task.FromResult(new ToolReconciliationResult(
            VerificationDisposition.Inconclusive,
            false,
            "This tool has no deterministic reconciliation adapter."));
    }

    Task<ToolCompensationResult> CompensateAsync(
        PreparedToolRequest request,
        CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        return Task.FromResult(new ToolCompensationResult(
            false,
            false,
            "This tool does not declare a compensation operation."));
    }
}

/// <summary>
/// Declares local capture and external data-flow capabilities separately.
/// Model output cannot alter this policy; the orchestrator uses it to force
/// consent even when Auto/Skip or a stored tool grant would otherwise allow.
/// </summary>
public interface ISensitiveDataTool
{
    ToolDataFlowPolicy DataFlowPolicy { get; }
}

public interface IMcpToolHost
{
    IReadOnlyList<IMcpTool> GetRegisteredTools();
    IMcpTool? Resolve(string name);
    IReadOnlyList<GatewayToolDefinition> GetToolDefinitions();
}
