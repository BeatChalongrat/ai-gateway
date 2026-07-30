using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class BrowserClickTool : IMcpTool, IPerceptualActionTool
{
    private readonly IBrowserBridgeService _bridge;
    private readonly BrowserInteractionObserver _observer;

    public BrowserClickTool(IBrowserBridgeService bridge)
    {
        _bridge = bridge;
        _observer = new BrowserInteractionObserver(bridge);
    }

    public string Name => "browser_click";
    public string Description =>
        "Click a DOM element by its current page ref and verify that exact target activation occurred.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "ref": { "type": "integer", "description": "Element ref from browser_read_page / browser_find" },
                "tab_id": { "type": "integer", "description": "Tab id from browser_read_page" },
                "observation_id": { "type": "string", "description": "Observation id from browser_read_page" },
                "dom_generation": { "type": "integer", "description": "DOM generation from browser_read_page" },
                "target_description": { "type": "string", "description": "Semantic accessible name used only to re-ground a stale ref" },
                "role": { "type": "string", "description": "Optional semantic role used during stale-ref recovery" },
                "expected_state": { "type": "string", "description": "Visible state expected after activation" }
            },
            "required": ["ref", "tab_id", "observation_id", "dom_generation", "expected_state"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("ref", out var refEl)
            || !refEl.TryGetInt32(out var elementRef))
            return ToolResult.Fail("ref integer is required.");
        var observationId = ToolHelpers.GetString(arguments, "observation_id");
        var expectedState = ToolHelpers.GetString(arguments, "expected_state");
        if (string.IsNullOrWhiteSpace(observationId)
            || string.IsNullOrWhiteSpace(expectedState)
            || !arguments.TryGetProperty("dom_generation", out var generationElement)
            || !generationElement.TryGetInt64(out var domGeneration))
            return ToolResult.Fail(
                "StaleDomReference: observation_id, dom_generation, and expected_state are required.");

        var tabId = BrowserToolHelpers.GetTabId(arguments);
        try
        {
            var result = await _bridge.RequestAsync(
                "page.click",
                ClickRequest(
                    elementRef,
                    tabId,
                    observationId,
                    domGeneration,
                    expectedState),
                ct);
            return ResultFrom(result, elementRef, recovered: false);
        }
        catch (BrowserBridgeException ex) when (
            ex.Message.Contains("StaleDomReference", StringComparison.Ordinal))
        {
            var target = ToolHelpers.GetString(arguments, "target_description");
            if (string.IsNullOrWhiteSpace(target))
                return ToolResult.Fail(
                    "StaleDomReference: semantic target_description is required to re-ground.");
            var recovery = InteractionRecoveryPolicy.Decide(
                "StaleDomReference",
                possibleSideEffect: false,
                actionSignature: $"browser_click:{tabId}:{target}",
                new InteractionRecoveryState(
                    1, 0, 1, 1, DateTimeOffset.UtcNow,
                    [$"browser_click:{tabId}:{target}"]),
                new InteractionRecoveryBudget(
                    MaximumAttemptsPerAction: 2,
                    MaximumRecoveryAttemptsPerRun: 2,
                    MaximumObservationsPerAction: 3,
                    MaximumIdenticalActions: 2,
                    MaximumWallClock: TimeSpan.FromSeconds(5)));
            if (!recovery.Allowed || !recovery.Reobserve || !recovery.Reground)
                return ToolResult.Fail($"{recovery.FailureCode}: {recovery.Reason}");

            try
            {
                var resolved = await _bridge.RequestAsync(
                    "page.resolve",
                    new
                    {
                        tab_id = tabId,
                        text_hints = new[] { target },
                        role = ToolHelpers.GetString(arguments, "role") ?? ""
                    },
                    ct);
                if (!resolved.TryGetProperty("candidates", out var candidates)
                    || candidates.ValueKind != JsonValueKind.Array
                    || candidates.GetArrayLength() != 1)
                    return ToolResult.Fail(
                        "TargetAmbiguous: stale target recovery requires exactly one semantic match.");
                var candidate = candidates[0];
                var freshRef = BrowserToolHelpers.GetInt(candidate, "ref");
                var freshObservation = BrowserToolHelpers.GetString(
                    resolved,
                    "observation_id");
                var freshGeneration = BrowserToolHelpers.GetLong(
                    resolved,
                    "dom_generation");
                var freshTab = BrowserToolHelpers.GetInt(resolved, "tab_id");
                if (freshRef < 0
                    || string.IsNullOrWhiteSpace(freshObservation)
                    || freshGeneration <= 0
                    || freshTab <= 0)
                    return ToolResult.Fail(
                        "StaleDomReference: fresh semantic resolution was not observation-bound.");
                var retried = await _bridge.RequestAsync(
                    "page.click",
                    ClickRequest(
                        freshRef,
                        freshTab,
                        freshObservation,
                        freshGeneration,
                        expectedState),
                    ct);
                return ResultFrom(retried, freshRef, recovered: true);
            }
            catch (Exception recoveryError) when (
                recoveryError is BrowserBridgeException
                    or BrowserNotConnectedException or TimeoutException)
            {
                return BrowserToolHelpers.DispatchFailure(recoveryError, mutating: true);
            }
        }
        catch (Exception ex) when (
            ex is BrowserNotConnectedException or TimeoutException or BrowserBridgeException)
        {
            return BrowserToolHelpers.DispatchFailure(ex, mutating: true);
        }
    }

    private static object ClickRequest(
        int elementRef,
        int? tabId,
        string observationId,
        long domGeneration,
        string expectedState) =>
        new
        {
            @ref = elementRef,
            tab_id = tabId,
            observation_id = observationId,
            dom_generation = domGeneration,
            expected_state = expectedState
        };

    private static ToolResult ResultFrom(
        JsonElement result,
        int elementRef,
        bool recovered)
    {
        var navigated = result.TryGetProperty("navigated", out var nav)
                        && nav.ValueKind == JsonValueKind.True;
        var activated = result.TryGetProperty("target_activated", out var active)
                        && active.ValueKind == JsonValueKind.True;
        var effectObserved = result.TryGetProperty("effect_observed", out var effect)
                             && effect.ValueKind == JsonValueKind.True;
        var expectedObserved =
            result.TryGetProperty("expected_state_observed", out var expected)
            && expected.ValueKind == JsonValueKind.True;
        var content = navigated
            ? $"Clicked [{elementRef}] - navigated to: " +
              $"{BrowserToolHelpers.GetString(result, "title")} - " +
              $"{BrowserToolHelpers.GetString(result, "url")}. " +
              "Refs are now stale; call browser_read_page again."
            : $"Clicked [{elementRef}].";
        if (!activated || (!effectObserved && !expectedObserved))
            return ToolResult.ExecutedUnverified(
                content,
                "DomPostconditionNotObserved",
                "The DOM bridge dispatched the click but did not observe the expected page state.");
        return ToolResult.Verified(
            content,
            [$"dom_ref={elementRef}", "target_activated=true",
                "browser_postcondition_observed=true",
                $"stale_target_recovered={recovered.ToString().ToLowerInvariant()}",
                $"navigated={navigated.ToString().ToLowerInvariant()}"]);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.Click,
            $"dom-ref:{BrowserToolHelpers.GetInt(arguments, "ref")}",
            ToolHelpers.GetString(arguments, "expected_state") ?? "",
            SideEffectClass.ReversibleMediumRisk,
            Reversible: false,
            Idempotent: false,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(3),
            MaximumAttempts: 2);

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
