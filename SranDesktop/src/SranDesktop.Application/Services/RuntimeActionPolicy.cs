using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public sealed record GoalRelevanceDecision(bool Allowed, string? FailureCode, string Reason);

public sealed class GoalRelevanceGate
{
    public GoalRelevanceDecision Evaluate(
        GoalSpec goal,
        TaskOutcome outcome,
        TaskStep step,
        string toolName,
        string argumentsJson)
    {
        if (toolName.StartsWith("mock_", StringComparison.Ordinal)
            || toolName.StartsWith("fake_", StringComparison.Ordinal)
            || toolName == "update_plan")
            return Allowed("Deterministic test/meta capability is scoped by its runtime caller.");
        // MCP operations are scoped by connector trust and the approved per-connector
        // risk policy, which are stricter and more specific than this desktop-outcome
        // taxonomy. Judging "resources/list" against OutcomeKind.TextValue would deny
        // every connector operation for reasons that say nothing about connectors.
        if (toolName.StartsWith("mcp.", StringComparison.Ordinal))
            return Allowed(
                "MCP operations are scoped by connector trust and the approved risk policy.");
        // Document operations are scoped by the workspace broker, the format
        // capability matrix, and a reopen-and-validate gate before anything is
        // published — all stricter and more specific than this desktop-outcome
        // taxonomy. Judging "document" against OutcomeKind.ApplicationReady denies
        // every scheduled and sub-agent document task for a reason that is about
        // GUI readiness and says nothing about documents.
        if (toolName == "document")
            return Allowed(
                "Document operations are scoped by the workspace broker and validated by "
                + "reopening the result before it is published.");
        if (CapabilityRouter.IsObservationTool(toolName))
            return ObservationRelevant(outcome, toolName, argumentsJson)
                ? Allowed("Observation is scoped to the current outcome or verification plan.")
                : Denied("GoalIrrelevantAction",
                    "The requested observation does not support the current outcome.");

        // open_application is a read-mostly enabling precondition: it only makes an
        // application available and never mutates task state. It must stay relevant for
        // any outcome that first requires the app to be brought up (ready / text-entry /
        // file / browser / generic) — binding it to outcome.Kind (as the switch below
        // does) deadlocks a task whose first outcome is TextValue/FileState but which
        // must open the app before input can land. It is NOT, however, a valid action
        // toward an operation or observation outcome: those presuppose the app is already
        // open and require a distinct effect, so re-opening cannot satisfy them
        // (RS-001 guards this). This is a RELEVANCE allowance only — consent/approval for
        // real side-effect actions is unchanged.
        if (toolName == "open_application"
            && outcome.Kind is not OutcomeKind.OperationProcessed
                and not OutcomeKind.StateObserved)
            return Allowed("Opening an application is a universal enabling precondition.");

        var relevant = outcome.Kind switch
        {
            OutcomeKind.ApplicationReady => toolName == "open_application",
            OutcomeKind.OperationProcessed => toolName is "uia_click" or "computer_click"
                or "computer_key" or "browser_click",
            OutcomeKind.StateObserved => false,
            OutcomeKind.TextValue => toolName is "uia_set_value" or "browser_type"
                or "computer_type" or "write_file" or "edit_file",
            OutcomeKind.BrowserState => toolName.StartsWith("browser_", StringComparison.Ordinal)
                && SpecializedBrowserActionMatches(outcome, toolName),
            OutcomeKind.ProcessState => toolName is "run_command" or "computer_click",
            OutcomeKind.FileState => toolName is "read_file" or "write_file" or "edit_file"
                or "move_file" or "list_directory" or "search_files",
            OutcomeKind.ConstraintPreserved => CapabilityRouter.IsObservationTool(toolName),
            _ => GenericMatch(goal, step, toolName, argumentsJson)
        };
        if (!relevant)
            return Denied("GoalIrrelevantAction",
                $"Capability '{toolName}' is not relevant to outcome '{outcome.Description}'.");

        // Pre-dispatch only: the resolved window HWND/PID is not available here,
        // so the arguments-name match is a coarse relevance HINT, not proof of the
        // right window. It stays conservative (deny when the name is absent); the
        // authoritative, HWND/PID-primary window-identity check runs at verify time
        // in SemanticOutcomeMatcher (a matching name alone can never verify).
        if (outcome.ExpectedApplication is { Length: > 0 } application
            && IsDesktopInput(toolName)
            && !argumentsJson.Contains(application, StringComparison.OrdinalIgnoreCase))
            return Denied("WrongWindow",
                "Desktop input arguments do not name the outcome's expected application.");
        return Allowed("The action is directly relevant to the current outcome.");
    }

    public static bool IsToolRelevant(TaskOutcome outcome, string toolName) =>
        toolName.StartsWith("mcp.", StringComparison.Ordinal)
        || toolName == "document"
        || outcome.Kind switch
        {
            OutcomeKind.ApplicationReady => toolName == "open_application",
            OutcomeKind.OperationProcessed => toolName is "uia_click" or "computer_click"
                or "computer_key" or "browser_click",
            OutcomeKind.StateObserved => CapabilityRouter.IsObservationTool(toolName),
            OutcomeKind.TextValue => toolName is "uia_set_value" or "browser_type"
                or "computer_type" or "write_file" or "edit_file",
            OutcomeKind.BrowserState => toolName.StartsWith("browser_", StringComparison.Ordinal)
                && SpecializedBrowserActionMatches(outcome, toolName),
            OutcomeKind.ProcessState => toolName == "run_command",
            OutcomeKind.FileState => toolName is "read_file" or "write_file" or "edit_file"
                or "move_file" or "list_directory" or "search_files",
            _ => true
        };

    private static bool ObservationRelevant(
        TaskOutcome outcome,
        string toolName,
        string argumentsJson)
    {
        if (outcome.Kind == OutcomeKind.StateObserved)
            return toolName is "computer_screenshot" or "get_computer_state"
                or "read_screen_elements" or "browser_read_page" or "browser_screenshot";
        if (outcome.Kind == OutcomeKind.ApplicationReady)
            return toolName is "get_computer_state" or "read_screen_elements"
                or "computer_screenshot";
        if (outcome.Kind == OutcomeKind.FileState)
            return toolName is "read_file" or "list_directory" or "search_files";
        if (outcome.Kind == OutcomeKind.BrowserState)
            return toolName is "browser_read_page" or "browser_list_tabs" or "browser_screenshot";
        if (outcome.Kind == OutcomeKind.TextValue)
            return toolName is "read_screen_elements" or "browser_read_page";
        return argumentsJson.Contains(outcome.ExpectedApplication ?? "\0",
                   StringComparison.OrdinalIgnoreCase)
               || toolName == "get_computer_state";
    }

    private static bool GenericMatch(
        GoalSpec goal,
        TaskStep step,
        string toolName,
        string argumentsJson)
    {
        var semantic = string.Join(' ', goal.NormalizedGoal, step.Description,
            step.IntendedOutcome).ToLowerInvariant();
        if (toolName == "run_command")
            return ContainsAny(semantic, "run", "build", "test", "command", "terminal", "project",
                "install");
        if (toolName.Contains("file", StringComparison.Ordinal))
            return ContainsAny(semantic, "file", "folder", "project", "report", "document");
        if (toolName.StartsWith("browser_", StringComparison.Ordinal))
            return ContainsAny(semantic, "browser", "website", "web", "form", "url");
        if (toolName.StartsWith("computer_", StringComparison.Ordinal)
            || toolName.StartsWith("uia_", StringComparison.Ordinal)
            || toolName == "open_application")
            return ContainsAny(semantic, "open", "application", "window", "click", "type", "close",
                "activate", "press", "select", "toggle", "scroll", "drag", "focus",
                "set", "fill", "enter", "input", "replace",
                "project", "build", "test");
        return argumentsJson.Length > 2;
    }

    private static bool IsDesktopInput(string toolName) =>
        toolName is "computer_click" or "computer_type" or "computer_key"
            or "computer_drag" or "computer_scroll" or "uia_click" or "uia_set_value";

    private static bool SpecializedBrowserActionMatches(
        TaskOutcome outcome,
        string toolName)
    {
        var semantic = $"{outcome.Description} {outcome.IntendedState}";
        return toolName switch
        {
            "browser_upload" => ContainsAny(
                semantic.ToLowerInvariant(),
                "upload",
                "อัปโหลด"),
            "browser_download" => ContainsAny(
                semantic.ToLowerInvariant(),
                "download",
                "ดาวน์โหลด"),
            _ => true
        };
    }

    private static bool ContainsAny(string value, params string[] terms) => terms.Any(value.Contains);
    private static GoalRelevanceDecision Allowed(string reason) => new(true, null, reason);
    private static GoalRelevanceDecision Denied(string code, string reason) => new(false, code, reason);
}
