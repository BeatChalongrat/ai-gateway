using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Domain.Entities;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class BrowserTypeTool : IMcpTool, IPerceptualActionTool
{
    private readonly IBrowserBridgeService _bridge;
    private readonly BrowserInteractionObserver _observer;

    public BrowserTypeTool(IBrowserBridgeService bridge)
    {
        _bridge = bridge;
        _observer = new BrowserInteractionObserver(bridge);
    }

    public string Name => "browser_type";
    public string Description =>
        "Set a DOM field by its current page ref and verify target focus plus exact value read-back.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "ref": { "type": "integer", "description": "Element ref from browser_read_page / browser_find" },
                "text": { "type": "string", "description": "The text to type" },
                "clear": { "type": "boolean", "description": "Replace the current value first (default true)" },
                "submit": { "type": "boolean", "description": "Press Enter after typing (default false)" },
                "tab_id": { "type": "integer", "description": "Tab id from browser_read_page" },
                "observation_id": { "type": "string", "description": "Observation id from browser_read_page" },
                "dom_generation": { "type": "integer", "description": "DOM generation from browser_read_page" },
                "expected_state": { "type": "string", "description": "Required after submit; optional for value-only typing" }
            },
            "required": ["ref", "text", "tab_id", "observation_id", "dom_generation"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("ref", out var refEl)
            || !refEl.TryGetInt32(out var elementRef))
            return Task.FromResult(ToolResult.Fail("ref integer is required."));
        var text = ToolHelpers.GetString(arguments, "text");
        if (text is null)
            return Task.FromResult(ToolResult.Fail("text is required."));
        var observationId = ToolHelpers.GetString(arguments, "observation_id");
        if (string.IsNullOrWhiteSpace(observationId)
            || !arguments.TryGetProperty("dom_generation", out var generationElement)
            || !generationElement.TryGetInt64(out var domGeneration))
            return Task.FromResult(ToolResult.Fail(
                "StaleDomReference: observation_id and dom_generation are required."));
        var submit = ToolHelpers.GetBool(arguments, "submit");
        var expectedState = ToolHelpers.GetString(arguments, "expected_state");
        if (submit && string.IsNullOrWhiteSpace(expectedState))
            return Task.FromResult(ToolResult.Fail(
                "SubmitRequiresPostcondition: expected_state is required for form submission."));
        if (Application.Services.CredentialTextGuard.LooksLikeCredential(text, out var kind))
            return Task.FromResult(ToolResult.Fail(
                $"Refused: the text looks like a {kind}. Typing sensitive data is prohibited; " +
                "the user must enter it."));

        return BrowserToolHelpers.RequestAsync(
            _bridge,
            "page.type",
            new
            {
                @ref = elementRef,
                text,
                clear = ToolHelpers.GetBool(arguments, "clear", fallback: true),
                submit,
                tab_id = BrowserToolHelpers.GetTabId(arguments),
                observation_id = observationId,
                dom_generation = domGeneration,
                expected_state = expectedState ?? ""
            },
            result =>
            {
                var preview = text.Length > 60 ? text[..60] + "..." : text;
                var valueVerified = result.TryGetProperty("value_verified", out var verified)
                                    && verified.ValueKind == JsonValueKind.True;
                var activated = result.TryGetProperty("target_activated", out var active)
                                && active.ValueKind == JsonValueKind.True;
                var submitVerified = result.TryGetProperty("submit_verified", out var submitted)
                                     && submitted.ValueKind == JsonValueKind.True;
                if (!valueVerified || !activated || (submit && !submitVerified))
                    return ToolResult.ExecutedUnverified(
                        $"Typed into [{elementRef}]: \"{preview}\".",
                        "DomValueNotVerified",
                        submit
                            ? "The value was set, but the form submission postcondition was not observed."
                            : "The DOM bridge did not confirm target focus and exact value read-back.");
                return ToolResult.Verified(
                    $"Typed and verified [{elementRef}]: \"{preview}\".",
                    [$"dom_ref={elementRef}", "target_activated=true", "value_read_back=true",
                        $"submit_verified={submitVerified.ToString().ToLowerInvariant()}"]);
            },
            ct,
            mutating: true);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments)
    {
        var submit = ToolHelpers.GetBool(arguments, "submit");
        return new InteractionActionDescriptor(
            submit ? InteractionActionKind.Submit : InteractionActionKind.TypeText,
            $"dom-ref:{BrowserToolHelpers.GetInt(arguments, "ref")}",
            ToolHelpers.GetString(arguments, "expected_state")
            ?? ToolHelpers.GetString(arguments, "text")
            ?? "",
            submit ? SideEffectClass.Consequential : SideEffectClass.ReversibleLowRisk,
            Reversible: !submit,
            Idempotent: !submit && ToolHelpers.GetBool(arguments, "clear", true),
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(3),
            MaximumAttempts: submit ? 1 : 2);
    }

    public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct) =>
        _observer.ObserveBeforeAsync(arguments, DescribeAction(arguments), ct);

    public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct) =>
        _observer.ObserveAfterAsync(arguments, DescribeAction(arguments), before, ct);

    public ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after) =>
        _observer.Verify(result, before, after);
}
