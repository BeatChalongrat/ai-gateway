using System.Text.Json;

namespace SranDesktop.Shell.Bridge;

/// <summary>
/// The Phase 5 control vocabulary (SHELL_PHASE5_REPORT.md §2.2): the ONE new
/// page↔host request/response pair, <c>host.query</c> / <c>host.result</c>,
/// added so the desktop-only screens can read and write host-local state.
///
/// It is deliberately SEPARATE from the turn vocabulary. The four-in/four-out
/// <see cref="InboundMessageType"/>/<see cref="OutboundMessageType"/> is
/// untouched and its Phase 3 pinning test passes unchanged; this pair is its
/// own closed one-in/one-out set, pinned by
/// <c>ShellPinningTests.The_Control_Vocabulary_Is_Exactly_One_In_One_Out</c>.
///
/// The security property is NOT the count. It is that <see cref="HostQueryOp"/>
/// is a CLOSED enum with every operation named individually — no generic method
/// dispatch, no path parameter naming a file the page chose. An op outside the
/// set is dropped and logged; it never becomes a best-effort call. Pinned by
/// <c>ShellPinningTests.The_Host_Query_Op_Set_Is_Closed_And_Named</c>.
/// </summary>
public enum ControlInboundType
{
    HostQuery,
}

public enum ControlOutboundType
{
    HostResult,
}

/// <summary>
/// Every privileged read/write the desktop-only screens need, named one by one.
/// Grouped only for reading; the wire form is the dotted string in
/// <see cref="HostQueryProtocol.ParseOp"/>. Adding a member here without adding
/// it to the pinning test fails the test — that is the point.
/// </summary>
public enum HostQueryOp
{
    // skills — %APPDATA%\SRAN Desktop\skills (ISkillManager)
    SkillsList,
    SkillsSetEnabled,
    SkillsInstallPicker,   // native OpenFileDialog; no path crosses the bridge
    SkillsDelete,

    // scheduled tasks — SQLite + in-proc timer (IScheduledTaskStore/ITaskSchedulerService)
    TasksList,
    TasksCreate,
    TasksUpdate,
    TasksSetEnabled,
    TasksDelete,
    TasksRunNow,

    // MCP connectors — local config + stdio children (IMcpConnectorService)
    ConnectorsListCatalog,
    ConnectorsListInstalled,
    ConnectorsInstall,        // api_key → NATIVE key dialog in host; no secret on the bridge
    ConnectorsUninstall,
    ConnectorsOauthConnect,   // system browser (host-driven)
    ConnectorsOauthDisconnect,
    ConnectorsAddCustomDialog, // NATIVE dialog; page names no process (§2.4)

    // local settings (ISettingsService) — SettingKey allow-list gates writes
    SettingsGet,
    SettingsSet,

    // folder grants — the act of granting reads a path from the OS, never the page
    FoldersList,
    FoldersPickAndGrant,   // native OpenFolderDialog; NO path argument
    FoldersRevoke,         // must match an existing grant exactly

    // local global agent notes (IConversationStore)
    GlobalNotesList,
    GlobalNotesDelete,

    // renderer-side error reporting → CrashReportService (§4.2)
    DiagReportRendererError,
}

/// <summary>
/// The CLOSED allow-list of local settings keys <c>settings.set</c> may write
/// (§2.3). A key outside this set is dropped and logged. Deliberately excludes
/// <c>granted_folders</c> (folders.* + native picker only), <c>approval_mode</c>
/// (a separate native-gated decision), and every DPAPI/secret field.
/// </summary>
public enum SettingKey
{
    Theme,
    MinimizeToTray,
    StartWithWindows,
    CrashReportingEnabled,
    SyncAgentConversations,
    PreferLocalOfficeGeneration,
    DefaultModel,
    ChatEdgeBaseUrl,
    GatewayBaseUrl,
    BrowserExtensionId,
    WritingStyleProfile,
    Locale,
}

/// <summary>One parsed host.query envelope.</summary>
public sealed record HostQuery(string RequestId, HostQueryOp Op, JsonElement Args);

/// <summary>
/// Pure wire parsing for the control pair (no WebView2 types, so every rejection
/// path is unit-testable). Mirrors <see cref="BridgeProtocol"/>: every Try*
/// returns null on ANY structural deviation, never a best-effort value.
/// </summary>
public static class HostQueryProtocol
{
    /// <summary>Map the wire <c>op</c> string. Closed: anything else is null.</summary>
    public static HostQueryOp? ParseOp(string? op) => op switch
    {
        "skills.list" => HostQueryOp.SkillsList,
        "skills.setEnabled" => HostQueryOp.SkillsSetEnabled,
        "skills.installPicker" => HostQueryOp.SkillsInstallPicker,
        "skills.delete" => HostQueryOp.SkillsDelete,
        "tasks.list" => HostQueryOp.TasksList,
        "tasks.create" => HostQueryOp.TasksCreate,
        "tasks.update" => HostQueryOp.TasksUpdate,
        "tasks.setEnabled" => HostQueryOp.TasksSetEnabled,
        "tasks.delete" => HostQueryOp.TasksDelete,
        "tasks.runNow" => HostQueryOp.TasksRunNow,
        "connectors.listCatalog" => HostQueryOp.ConnectorsListCatalog,
        "connectors.listInstalled" => HostQueryOp.ConnectorsListInstalled,
        "connectors.install" => HostQueryOp.ConnectorsInstall,
        "connectors.uninstall" => HostQueryOp.ConnectorsUninstall,
        "connectors.oauthConnect" => HostQueryOp.ConnectorsOauthConnect,
        "connectors.oauthDisconnect" => HostQueryOp.ConnectorsOauthDisconnect,
        "connectors.addCustomDialog" => HostQueryOp.ConnectorsAddCustomDialog,
        "settings.get" => HostQueryOp.SettingsGet,
        "settings.set" => HostQueryOp.SettingsSet,
        "folders.list" => HostQueryOp.FoldersList,
        "folders.pickAndGrant" => HostQueryOp.FoldersPickAndGrant,
        "folders.revoke" => HostQueryOp.FoldersRevoke,
        "globalNotes.list" => HostQueryOp.GlobalNotesList,
        "globalNotes.delete" => HostQueryOp.GlobalNotesDelete,
        "diag.reportRendererError" => HostQueryOp.DiagReportRendererError,
        _ => null,
    };

    /// <summary>Map the wire settings key. Closed: anything else is null → the
    /// set is dropped and logged (§2.3, criterion 4).</summary>
    public static SettingKey? ParseSettingKey(string? key) => key switch
    {
        "theme" => SettingKey.Theme,
        "minimizeToTray" => SettingKey.MinimizeToTray,
        "startWithWindows" => SettingKey.StartWithWindows,
        "crashReportingEnabled" => SettingKey.CrashReportingEnabled,
        "syncAgentConversations" => SettingKey.SyncAgentConversations,
        "preferLocalOfficeGeneration" => SettingKey.PreferLocalOfficeGeneration,
        "defaultModel" => SettingKey.DefaultModel,
        "chatEdgeBaseUrl" => SettingKey.ChatEdgeBaseUrl,
        "gatewayBaseUrl" => SettingKey.GatewayBaseUrl,
        "browserExtensionId" => SettingKey.BrowserExtensionId,
        "writingStyleProfile" => SettingKey.WritingStyleProfile,
        "locale" => SettingKey.Locale,
        _ => null,
    };

    public static string ResultWireName => "host.result";

    /// <summary>
    /// Parse a <c>host.query</c> payload: <c>{ requestId, op, args? }</c>. The
    /// envelope's nonce/origin are already validated by the shared guards in
    /// BridgeChannel (C-2/C-3) before this runs. Returns null (with a reason) on
    /// any deviation — a missing requestId, a non-string op, or an op outside
    /// the closed set.
    /// </summary>
    public static HostQuery? TryParseHostQuery(JsonElement payload, out string reason)
    {
        reason = "";
        if (payload.ValueKind != JsonValueKind.Object)
        {
            reason = "payload-not-object";
            return null;
        }
        if (!TryGetString(payload, "requestId", out var requestId) || requestId.Length is 0 or > 128)
        {
            reason = "requestId-invalid";
            return null;
        }
        if (!TryGetString(payload, "op", out var opString))
        {
            reason = "op-missing";
            return null;
        }
        var op = ParseOp(opString);
        if (op is null)
        {
            reason = $"unknown-op:{opString}";
            return null;
        }
        var args = payload.TryGetProperty("args", out var argsEl) ? argsEl.Clone() : default;
        return new HostQuery(requestId, op.Value, args);
    }

    // ── Per-op argument parsers (structural refusals; resolution-based refusals
    //    — skillId resolves, builtin, cron valid — live in the dispatcher) ──

    public static bool TryStringArg(JsonElement args, string name, out string value)
    {
        value = "";
        if (args.ValueKind != JsonValueKind.Object)
            return false;
        return TryGetString(args, name, out value) && value.Length is > 0 and <= 512;
    }

    public static bool TryBoolArg(JsonElement args, string name, out bool value)
    {
        value = false;
        if (args.ValueKind != JsonValueKind.Object || !args.TryGetProperty(name, out var el))
            return false;
        if (el.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            value = el.GetBoolean();
            return true;
        }
        return false;
    }

    /// <summary>Value validation for <c>settings.set</c>, keyed by the closed
    /// <see cref="SettingKey"/>. Returns the normalized string form the host
    /// applies, or null when the value is the wrong shape for the key.</summary>
    public static bool TryReadSettingValue(SettingKey key, JsonElement args, out object? value)
    {
        value = null;
        if (args.ValueKind != JsonValueKind.Object || !args.TryGetProperty("value", out var v))
            return false;

        switch (key)
        {
            case SettingKey.MinimizeToTray:
            case SettingKey.StartWithWindows:
            case SettingKey.CrashReportingEnabled:
            case SettingKey.SyncAgentConversations:
            case SettingKey.PreferLocalOfficeGeneration:
                if (v.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                    return false;
                value = v.GetBoolean();
                return true;

            case SettingKey.Theme:
                if (v.ValueKind != JsonValueKind.String) return false;
                var theme = v.GetString();
                if (theme is not ("Dark" or "Light" or "System")) return false;
                value = theme;
                return true;

            case SettingKey.ChatEdgeBaseUrl:
            case SettingKey.GatewayBaseUrl:
                if (v.ValueKind != JsonValueKind.String) return false;
                var url = v.GetString()!.Trim();
                if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
                    || uri.Scheme is not ("http" or "https"))
                    return false;
                value = url.TrimEnd('/');
                return true;

            case SettingKey.DefaultModel:
            case SettingKey.BrowserExtensionId:
            case SettingKey.Locale:
                if (v.ValueKind != JsonValueKind.String) return false;
                var s = v.GetString()!;
                if (s.Length > 256) return false;
                value = s;
                return true;

            case SettingKey.WritingStyleProfile:
                if (v.ValueKind != JsonValueKind.String) return false;
                var profile = v.GetString()!;
                if (profile.Length > 8000) return false;
                value = profile;
                return true;

            default:
                return false;
        }
    }

    private static bool TryGetString(JsonElement obj, string name, out string value)
    {
        value = "";
        if (!obj.TryGetProperty(name, out var el) || el.ValueKind != JsonValueKind.String)
            return false;
        value = el.GetString()!;
        return true;
    }
}
