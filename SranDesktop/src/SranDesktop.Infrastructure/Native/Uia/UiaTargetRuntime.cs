using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Native.Uia;

public sealed record TargetAcquisitionResult(
    TargetResolution Resolution,
    IReadOnlyList<TargetCandidate> Candidates,
    IReadOnlyList<CandidateScore> Scores,
    IReadOnlyList<string> Trace);

public sealed record TargetActivationResult(
    bool TargetReResolved,
    bool TargetValidated,
    bool InputExecuted,
    bool OsInputAccepted,
    bool TargetActivated,
    bool EffectVerified,
    bool TaskCompleted,
    string? BeforeState,
    string? AfterState,
    string? FailureCode,
    string? FailureReason,
    IReadOnlyList<string> Evidence);

public interface ITargetAcquisitionRuntime
{
    Task<TargetAcquisitionResult> ResolveAsync(
        CaptureContext capture,
        TargetIntent intent,
        VisionTarget? visionTarget,
        CancellationToken ct);

    Task<TargetActivationResult> ActivateAsync(
        CaptureContext capture,
        TargetIntent intent,
        TargetCandidate target,
        CancellationToken ct);

    Task<bool> ValidatePointAsync(
        CaptureContext capture,
        TargetIntent intent,
        TargetCandidate target,
        int x,
        int y,
        CancellationToken ct);

    /// <summary>
    /// UIA-point-first (UIA-first plan, Phase 1): activate the UI Automation element that
    /// sits under a raw screen point via its OWN pattern (Invoke/Toggle/Select/Expand) —
    /// this is what a coordinate click would have hit, but activated structurally and
    /// WITHOUT stealing foreground. Only acts when the element belongs to the capture's
    /// process and is enabled/on-screen; otherwise returns <c>TargetActivated=false</c>
    /// with code <c>NoPatternAtPoint</c> so the caller falls back to a coordinate click
    /// (canvas / games / custom-drawn controls with no UIA pattern).
    /// </summary>
    Task<TargetActivationResult> TryActivateAtPointAsync(
        CaptureContext capture,
        int x,
        int y,
        CancellationToken ct);
}

public sealed class WindowsTargetAcquisitionRuntime : ITargetAcquisitionRuntime
{
    private readonly TargetResolverOptions _options;
    private readonly IBrowserBridgeService? _browser;

    public WindowsTargetAcquisitionRuntime(
        TargetResolverOptions? options = null,
        IBrowserBridgeService? browser = null)
    {
        _options = options ?? new TargetResolverOptions();
        _browser = browser;
    }

    public async Task<TargetAcquisitionResult> ResolveAsync(
        CaptureContext capture,
        TargetIntent intent,
        VisionTarget? visionTarget,
        CancellationToken ct)
    {
        var candidates = new List<TargetCandidate>();
        if (_browser?.IsConnected == true)
            candidates.AddRange(await DiscoverDomAsync(capture, intent, ct));
        candidates.AddRange(await Task.Run(
                () => DiscoverUia(capture), ct)
            .WaitAsync(UiaToolSupport.UiaTimeout, ct));
        if (visionTarget is not null)
        {
            var vision = VisionTargetMapper.Map(
                capture, visionTarget, DateTimeOffset.UtcNow,
                _options.MinimumVisionConfidence);
            if (vision.Target is not null)
                candidates.Add(vision.Target);
        }

        var (resolution, scores) = TargetResolver.Resolve(
            intent, candidates, DateTimeOffset.UtcNow, _options);
        var trace = scores.Select(s =>
            $"candidate={s.Candidate.CandidateId};method={s.Candidate.Method};" +
            $"name={s.Candidate.AccessibleName};score={s.FinalScore:0.000};" +
            string.Join(";", s.Reasons)).ToArray();
        return new TargetAcquisitionResult(resolution, candidates, scores, trace);
    }

    public async Task<TargetActivationResult> ActivateAsync(
        CaptureContext capture,
        TargetIntent intent,
        TargetCandidate target,
        CancellationToken ct)
    {
        var preAction = ValidateCapture(capture, target);
        if (!preAction.IsValid)
            return FailedDom(preAction.FailureCode.ToString(), preAction.Reason);
        if (target.Method == TargetMethod.BrowserDom)
            return await ActivateDomAsync(intent, target, ct);
        return await Task.Run(() => Activate(capture, intent, target), ct)
            .WaitAsync(UiaToolSupport.UiaTimeout, ct);
    }

    public async Task<bool> ValidatePointAsync(
        CaptureContext capture,
        TargetIntent intent,
        TargetCandidate target,
        int x,
        int y,
        CancellationToken ct) =>
        await Task.Run(() =>
        {
            var element = AutomationElement.FromPoint(new System.Windows.Point(x, y));
            var current = element.Current;
            if (current.ProcessId != capture.ProcessId || current.IsOffscreen || !current.IsEnabled)
                return false;
            var type = current.ControlType.ProgrammaticName
                .Replace("ControlType.", "", StringComparison.Ordinal);
            var bounds = current.BoundingRectangle;
            var atPoint = new TargetCandidate(
                TargetMethod.WindowsUia, "point", type.ToLowerInvariant(), current.Name ?? "",
                capture.WindowHandle ?? 0, current.ProcessId,
                new PhysicalRect((int)bounds.X, (int)bounds.Y, (int)bounds.Width, (int)bounds.Height),
                .95, ReadState(element), "invoke", Array.Empty<string>(), DateTimeOffset.UtcNow,
                current.IsEnabled, !current.IsOffscreen, current.IsOffscreen,
                AutomationId: current.AutomationId, ControlType: type);
            return CandidateFusion.IoU(atPoint.BoundingBox, target.BoundingBox) > .25
                   && TargetScorer.Score(intent, atPoint).SemanticScore > 0;
        }, ct).WaitAsync(UiaToolSupport.UiaTimeout, ct);

    public async Task<TargetActivationResult> TryActivateAtPointAsync(
        CaptureContext capture, int x, int y, CancellationToken ct) =>
        await Task.Run(() => ActivateAtPoint(capture, x, y), ct)
            .WaitAsync(UiaToolSupport.UiaTimeout, ct);

    // Structural activation of the element under a raw point. Bound to the capture's
    // process — an element that belongs to another window (the host app, an occluding
    // window) yields NoPatternAtPoint so ComputerClickTool falls back to a coordinate
    // click, where the existing ValidateLive guards (host-window refusal, occlusion,
    // stale capture) still run. Reuses InvokeFirstPattern — the same pattern ladder the
    // semantic Activate path uses; no separate engine.
    private static TargetActivationResult ActivateAtPoint(CaptureContext capture, int x, int y)
    {
        AutomationElement? element;
        try
        {
            element = AutomationElement.FromPoint(new System.Windows.Point(x, y));
        }
        catch (Exception)
        {
            return NoPatternAtPoint("No UI Automation element could be resolved at the point.");
        }
        if (element is null)
            return NoPatternAtPoint("No UI Automation element could be resolved at the point.");

        try
        {
            var current = element.Current;
            if (capture.ProcessId is { } pid && current.ProcessId != pid)
                return NoPatternAtPoint("The element under the point belongs to a different process.");
            if (!current.IsEnabled || current.IsOffscreen)
                return NoPatternAtPoint("The element under the point is disabled or offscreen.");
            var before = ReadState(element);
            if (!InvokeFirstPattern(element, out var method))
                return NoPatternAtPoint("The element under the point exposes no actionable UIA pattern.");

            Thread.Sleep(100);
            string? after;
            try { after = ReadState(element); }
            catch (ElementNotAvailableException) { after = "disappeared"; }
            var effect = !string.Equals(before, after, StringComparison.Ordinal);
            return new TargetActivationResult(
                true, true, true, true, true, effect, effect,
                before, after, effect ? null : "EffectNotObserved",
                effect ? null
                    : "The element was activated via its UIA pattern but no state change was observed.",
                new[]
                {
                    $"uia_point_pattern={method}",
                    $"before={before}", $"after={after}",
                    $"pid={current.ProcessId}"
                });
        }
        catch (ElementNotAvailableException)
        {
            return NoPatternAtPoint("The element under the point disappeared before activation.");
        }
    }

    private static TargetActivationResult NoPatternAtPoint(string reason) =>
        new(false, false, false, false, false, false, false,
            null, null, "NoPatternAtPoint", reason, Array.Empty<string>());

    /// <summary>First applicable pattern wins — the shared pattern ladder used by both the
    /// semantic <see cref="Activate"/> path and UIA-point-first. Order: Toggle (checkbox),
    /// Invoke (button/menuitem), SelectionItem (radio/list/tab), ExpandCollapse (combo/menu).</summary>
    private static bool InvokeFirstPattern(AutomationElement element, out string method)
    {
        if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
        {
            ((TogglePattern)toggle).Toggle();
            method = "TogglePattern";
            return true;
        }
        if (element.TryGetCurrentPattern(InvokePattern.Pattern, out var invoke))
        {
            ((InvokePattern)invoke).Invoke();
            method = "InvokePattern";
            return true;
        }
        if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var selection))
        {
            ((SelectionItemPattern)selection).Select();
            method = "SelectionItemPattern";
            return true;
        }
        if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var expand))
        {
            ((ExpandCollapsePattern)expand).Expand();
            method = "ExpandCollapsePattern";
            return true;
        }
        method = "";
        return false;
    }

    private static List<TargetCandidate> DiscoverUia(CaptureContext capture)
    {
        if (capture.WindowHandle is not { } handle || capture.ProcessId is null)
            return new List<TargetCandidate>();
        var root = AutomationElement.FromHandle(new IntPtr(handle));
        var elements = root.FindAll(TreeScope.Descendants, Condition.TrueCondition);
        var candidates = new List<TargetCandidate>();
        foreach (AutomationElement element in elements)
        {
            try
            {
                var current = element.Current;
                if (current.ProcessId != capture.ProcessId)
                    continue;
                var bounds = current.BoundingRectangle;
                if (bounds.IsEmpty || bounds.Width < 1 || bounds.Height < 1)
                    continue;
                var actions = SupportedActions(element);
                if (actions.Count == 0)
                    continue;
                var type = current.ControlType.ProgrammaticName
                    .Replace("ControlType.", "", StringComparison.Ordinal);
                var parent = TreeWalker.ControlViewWalker.GetParent(element);
                var parentName = SafeName(parent);
                System.Windows.Point? clickable = null;
                try
                {
                    if (element.TryGetClickablePoint(out var point))
                        clickable = point;
                }
                catch (ElementNotAvailableException)
                {
                }

                var automationId = current.AutomationId ?? "";
                var name = current.Name ?? "";
                candidates.Add(new TargetCandidate(
                    TargetMethod.WindowsUia,
                    ElementId(automationId, name, type),
                    type.ToLowerInvariant(),
                    name,
                    handle,
                    current.ProcessId,
                    new PhysicalRect(
                        (int)Math.Round(bounds.X), (int)Math.Round(bounds.Y),
                        (int)Math.Round(bounds.Width), (int)Math.Round(bounds.Height)),
                    .96,
                    ReadState(element),
                    actions[0],
                    new[]
                    {
                        $"capture_id={capture.CaptureId}",
                        $"automation_id={automationId}",
                        $"help_text={current.HelpText}",
                        $"accelerator_key={current.AcceleratorKey}",
                        $"access_key={current.AccessKey}"
                    },
                    DateTimeOffset.UtcNow,
                    current.IsEnabled,
                    !current.IsOffscreen,
                    current.IsOffscreen,
                    CandidateId: ElementId(automationId, name, type),
                    CaptureId: capture.CaptureId,
                    AutomationId: automationId,
                    ControlType: type,
                    SupportedActions: actions,
                    TextContext: current.HelpText,
                    ParentContext: parentName,
                    NeighborContext: null,
                    RiskLevel: TargetRiskLevel.Low,
                    ClickablePoint: clickable));
            }
            catch (ElementNotAvailableException)
            {
            }
        }
        return candidates;
    }

    private async Task<IReadOnlyList<TargetCandidate>> DiscoverDomAsync(
        CaptureContext capture, TargetIntent intent, CancellationToken ct)
    {
        if (_browser is null)
            return Array.Empty<TargetCandidate>();
        var response = await _browser.RequestAsync("page.resolve", new
        {
            text_hints = intent.TextHints,
            role = intent.Role
        }, ct);
        if (!response.TryGetProperty("candidates", out var list)
            || list.ValueKind != System.Text.Json.JsonValueKind.Array)
            return Array.Empty<TargetCandidate>();
        var candidates = new List<TargetCandidate>();
        foreach (var item in list.EnumerateArray())
        {
            if (!item.TryGetProperty("bounds", out var bounds)
                || !TryDouble(bounds, "x", out var x)
                || !TryDouble(bounds, "y", out var y)
                || !TryDouble(bounds, "width", out var width)
                || !TryDouble(bounds, "height", out var height)
                || !item.TryGetProperty("ref", out var reference)
                || !reference.TryGetInt32(out var elementRef))
                continue;
            var candidateId = GetString(item, "candidate_id") ?? $"dom:{elementRef}";
            candidates.Add(new TargetCandidate(
                TargetMethod.BrowserDom,
                elementRef.ToString(),
                GetString(item, "role") ?? "custom",
                GetString(item, "accessible_name") ?? "",
                capture.WindowHandle ?? 0,
                capture.ProcessId ?? 0,
                new PhysicalRect(
                    capture.SourceRectPhysical.X + (int)Math.Round(x),
                    capture.SourceRectPhysical.Y + (int)Math.Round(y),
                    Math.Max(1, (int)Math.Round(width)),
                    Math.Max(1, (int)Math.Round(height))),
                .98,
                GetString(item, "current_state"),
                "click",
                new[]
                {
                    $"capture_id={capture.CaptureId}",
                    $"dom_ref={elementRef}",
                    "bounds_space=browser_viewport"
                },
                DateTimeOffset.UtcNow,
                item.TryGetProperty("enabled", out var enabled) && enabled.GetBoolean(),
                !item.TryGetProperty("visible", out var visible) || visible.GetBoolean(),
                false,
                CandidateId: candidateId,
                CaptureId: capture.CaptureId,
                AutomationId: GetString(item, "automation_id"),
                ControlType: GetString(item, "role"),
                SupportedActions: new[] { "click" },
                TextContext: GetString(item, "text_context"),
                ParentContext: GetString(item, "parent_context"),
                NeighborContext: GetString(item, "neighbor_context")));
        }
        return candidates;
    }

    private async Task<TargetActivationResult> ActivateDomAsync(
        TargetIntent intent, TargetCandidate target, CancellationToken ct)
    {
        if (_browser is null || !int.TryParse(target.ElementId, out var elementRef))
            return FailedDom("DomTargetStale", "DOM target reference is unavailable.");
        var response = await _browser.RequestAsync(
            "page.semantic_click", new { @ref = elementRef }, ct);
        var activated = response.TryGetProperty("target_activated", out var active)
                        && active.ValueKind == System.Text.Json.JsonValueKind.True;
        var before = GetString(response, "before_state");
        var after = GetString(response, "after_state");
        var effect = activated && (string.IsNullOrWhiteSpace(intent.DesiredState)
            ? !string.Equals(before, after, StringComparison.Ordinal)
            : VerifyDesiredState(
                TargetMethod.BrowserDom, target, before, after, intent.DesiredState));
        return new TargetActivationResult(
            true, true, true, true, activated, effect, effect,
            before, after, effect ? null : activated ? "EffectNotObserved" : "TargetActivationNotVerified",
            effect ? null : activated
                ? "DOM target activated but expected state was not observed."
                : "DOM did not confirm activation of the selected element.",
            new[]
            {
                $"method=BrowserDom",
                $"candidate_id={GetString(response, "candidate_id")}",
                $"before={before}",
                $"after={after}"
            });
    }

    private static TargetActivationResult FailedDom(string code, string reason) =>
        new(false, false, false, false, false, false, false,
            null, null, code, reason, Array.Empty<string>());

    private static string? GetString(System.Text.Json.JsonElement element, string name) =>
        element.TryGetProperty(name, out var value)
        && value.ValueKind == System.Text.Json.JsonValueKind.String
            ? value.GetString()
            : null;

    private static bool TryDouble(
        System.Text.Json.JsonElement element, string name, out double value)
    {
        value = 0;
        return element.TryGetProperty(name, out var property)
               && property.TryGetDouble(out value);
    }

    private static TargetActivationResult Activate(
        CaptureContext capture, TargetIntent intent, TargetCandidate target)
    {
        if (target.Method == TargetMethod.VisionBoundingBox)
            return Failed("VisionRequiresPhysicalClick",
                "Vision targets must pass safe-point and point-to-element validation.");
        var element = ReResolve(capture, target);
        if (element is null)
            return Failed("TargetStale", "The selected UIA target could not be re-resolved.");

        try
        {
            var current = element.Current;
            if (!current.IsEnabled || current.IsOffscreen)
                return Failed("TargetNotActionable", "The re-resolved target is disabled or offscreen.");
            var bounds = current.BoundingRectangle;
            if (!BoundsStable(target.BoundingBox, bounds, 3))
                return Failed("TargetGeometryChanged", "Target bounds changed after resolution.");
            var before = ReadState(element);
            if (!string.IsNullOrWhiteSpace(intent.DesiredState)
                && StateMatches(before, intent.DesiredState))
                return Success(before, before, "already_in_desired_state", true);

            if (!InvokeFirstPattern(element, out var method))
                return Failed("NoSemanticAction", "The target no longer exposes a supported UIA pattern.");

            Thread.Sleep(100);
            var afterElement = ReResolve(capture, target);
            if (afterElement is null)
                return Success(before, "disappeared", method, true);
            var after = ReadState(afterElement);
            var effect = string.IsNullOrWhiteSpace(intent.DesiredState)
                ? !string.Equals(before, after, StringComparison.Ordinal)
                : VerifyDesiredState(
                    TargetMethod.WindowsUia, target, before, after, intent.DesiredState);
            return Success(before, after, method, effect);
        }
        catch (ElementNotAvailableException)
        {
            return Failed("TargetStale", "The UIA target disappeared during activation.");
        }

        TargetActivationResult Success(
            string? before, string? after, string method, bool effect) =>
            new(true, true, true, true, true, effect, effect,
                before, after, effect ? null : "EffectNotObserved",
                effect ? null : "The target was activated but the expected state was not observed.",
                new[] { $"method={method}", $"before={before}", $"after={after}" });

        static TargetActivationResult Failed(string code, string reason) =>
            new(false, false, false, false, false, false, false,
                null, null, code, reason, Array.Empty<string>());
    }

    private static AutomationElement? ReResolve(
        CaptureContext capture, TargetCandidate target)
    {
        if (capture.WindowHandle is not { } handle)
            return null;
        var root = AutomationElement.FromHandle(new IntPtr(handle));
        var conditions = new List<Condition>
        {
            new PropertyCondition(AutomationElement.ProcessIdProperty, target.ProcessId),
            new PropertyCondition(AutomationElement.ControlTypeProperty,
                ControlType.LookupById(ControlTypeId(target.ControlType ?? target.Role)))
        };
        if (!string.IsNullOrWhiteSpace(target.AutomationId))
            conditions.Add(new PropertyCondition(
                AutomationElement.AutomationIdProperty, target.AutomationId));
        else
            conditions.Add(new PropertyCondition(
                AutomationElement.NameProperty, target.AccessibleName));
        var matches = root.FindAll(TreeScope.Descendants, new AndCondition(conditions.ToArray()));
        return matches.Count == 1 ? matches[0] : null;
    }

    private static CaptureValidationResult ValidateCapture(
        CaptureContext capture, TargetCandidate target)
    {
        var hWnd = capture.WindowHandle is { } handle ? new IntPtr(handle) : IntPtr.Zero;
        PhysicalRect? rect = null;
        int? pid = null;
        var exists = hWnd != IntPtr.Zero && Win32Input.IsWindow(hWnd);
        if (exists)
        {
            Win32Input.GetWindowThreadProcessId(hWnd, out var processId);
            pid = checked((int)processId);
            if (Win32Input.GetWindowRect(hWnd, out var current))
                rect = new PhysicalRect(
                    current.Left, current.Top,
                    current.Right - current.Left,
                    current.Bottom - current.Top);
        }
        var point = (
            target.BoundingBox.X + target.BoundingBox.Width / 2,
            target.BoundingBox.Y + target.BoundingBox.Height / 2);
        return CaptureActionValidator.Validate(
            capture, point,
            new LiveWindowState(
                DateTimeOffset.UtcNow, exists, pid, rect,
                Win32Input.DisplayTopologyVersion(),
                Win32Input.GetForegroundWindow().ToInt64(),
                Win32Input.RootWindowAtPoint(point.Item1, point.Item2).ToInt64(),
                Environment.ProcessId),
            new ComputerUseOptions());
    }

    private static int ControlTypeId(string type) =>
        type.ToLowerInvariant() switch
        {
            "button" => ControlType.Button.Id,
            "checkbox" => ControlType.CheckBox.Id,
            "radiobutton" => ControlType.RadioButton.Id,
            "edit" => ControlType.Edit.Id,
            "combobox" => ControlType.ComboBox.Id,
            "listitem" => ControlType.ListItem.Id,
            "menuitem" => ControlType.MenuItem.Id,
            "tabitem" => ControlType.TabItem.Id,
            "hyperlink" or "link" => ControlType.Hyperlink.Id,
            "treeitem" => ControlType.TreeItem.Id,
            "slider" => ControlType.Slider.Id,
            _ => ControlType.Custom.Id
        };

    private static List<string> SupportedActions(AutomationElement element)
    {
        var actions = new List<string>();
        if (element.TryGetCurrentPattern(TogglePattern.Pattern, out _)) actions.Add("toggle");
        if (element.TryGetCurrentPattern(InvokePattern.Pattern, out _)) actions.Add("invoke");
        if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out _)) actions.Add("select");
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out _)) actions.Add("set_value");
        if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out _)) actions.Add("expand");
        if (element.TryGetCurrentPattern(ScrollPattern.Pattern, out _)) actions.Add("scroll");
        return actions;
    }

    private static string? ReadState(AutomationElement element)
    {
        if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
            return ((TogglePattern)toggle).Current.ToggleState switch
            {
                ToggleState.On => "on",
                ToggleState.Off => "off",
                _ => "indeterminate"
            };
        if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var selection))
            return ((SelectionItemPattern)selection).Current.IsSelected ? "selected" : "not_selected";
        if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var expand))
            return ((ExpandCollapsePattern)expand).Current.ExpandCollapseState.ToString().ToLowerInvariant();
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
            return ((ValuePattern)value).Current.Value;
        return null;
    }

    private static bool StateMatches(string? actual, string expected)
    {
        if (string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
            return true;
        return expected.ToLowerInvariant() switch
        {
            "muted" or "checked" or "enabled" or "on" => actual == "on",
            "unmuted" or "unchecked" or "disabled" or "off" => actual == "off",
            _ => false
        };
    }

    private static bool VerifyDesiredState(
        TargetMethod method,
        TargetCandidate target,
        string? before,
        string? after,
        string desiredState)
    {
        var normalized = desiredState.ToLowerInvariant() switch
        {
            "muted" or "checked" or "enabled" => "on",
            "unmuted" or "unchecked" or "disabled" => "off",
            _ => desiredState
        };
        var type = method == TargetMethod.BrowserDom
            ? ExpectedEffectType.DomStateChanged
            : ExpectedEffectType.UiaPropertyChanged;
        return EffectVerifier.Verify(
            new ExpectedEffect(type, target.CandidateId ?? target.ElementId,
                "state", before, normalized),
            new EffectObservation(
                "post-action", type, target.CandidateId ?? target.ElementId,
                "state", before, after, true, 1,
                new[] { $"method={method}", $"before={before}", $"after={after}" }))
            .EffectVerified;
    }

    private static bool BoundsStable(
        PhysicalRect expected, System.Windows.Rect actual, int tolerance) =>
        Math.Abs(expected.X - actual.X) <= tolerance
        && Math.Abs(expected.Y - actual.Y) <= tolerance
        && Math.Abs(expected.Width - actual.Width) <= tolerance
        && Math.Abs(expected.Height - actual.Height) <= tolerance;

    private static string ElementId(string automationId, string name, string type) =>
        $"{type}|{automationId}|{name}";

    private static string? SafeName(AutomationElement? element)
    {
        try
        {
            return element?.Current.Name;
        }
        catch (ElementNotAvailableException)
        {
            return null;
        }
    }
}
