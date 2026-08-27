using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.ToolsSidecar;

/// <summary>
/// FIX1 §1.3 / FIX2 — the sidecar's SECOND, host-only RPC surface. It answers the
/// <c>host/query</c> JSON-RPC method (params <c>{ op, args }</c> → result
/// <c>{ ok, data, error }</c>), which the Electron host forwards the page's
/// <c>host.query</c> onto. It is a DIFFERENT method from <c>tools/call</c> and is
/// NEVER enumerated by <c>tools/list</c> — so the control plane is structurally
/// unreachable as a tool.
///
/// FIX2 ports the data/store half of <c>ShellHostQueryCoordinator</c> (skills,
/// tasks, connectors, globalNotes, settings) against the SAME Infrastructure
/// services the WPF app uses — same names, shapes, semantics; no re-derivation.
/// The pieces that need a WINDOW (native pickers, the add-custom flow), a TURN
/// (a scheduled task's run), or the user's SESSION (oauth) stay HOST-side; the
/// host reaches the sidecar's store/DPAPI work through host-only internal ops
/// (named with a leading <c>_</c>, never in the page's closed vocabulary).
/// </summary>
public sealed class HostQuerySurface
{
    private readonly ISettingsService _settings;
    private readonly ISkillManager _skills;
    private readonly IScheduledTaskStore _taskStore;
    private readonly IMcpConnectorService _connectors;
    private readonly IConversationStore _store;
    private readonly Func<object> _browserStatus;
    private readonly Func<string, (bool ok, object? data, string? error)> _applyExtensionFolder;
    private readonly Func<int?, Task<(bool ok, string? url, string? site)>> _browserSiteResolver;
    private readonly ILogger _logger;

    public HostQuerySurface(
        ISettingsService settings,
        ISkillManager skills,
        IScheduledTaskStore taskStore,
        IMcpConnectorService connectors,
        IConversationStore store,
        Func<object> browserStatus,
        Func<string, (bool ok, object? data, string? error)> applyExtensionFolder,
        Func<int?, Task<(bool ok, string? url, string? site)>> browserSiteResolver,
        ILogger logger)
    {
        _settings = settings;
        _skills = skills;
        _taskStore = taskStore;
        _connectors = connectors;
        _store = store;
        _browserStatus = browserStatus;
        _applyExtensionFolder = applyExtensionFolder;
        _browserSiteResolver = browserSiteResolver;
        _logger = logger;
    }

    public async Task<(bool ok, object? data, string? error)> DispatchAsync(string op, JsonObject? args)
    {
        try
        {
            return op switch
            {
                // ── settings (FIX1) ──────────────────────────────────────────
                "settings.get" => Ok(SettingsSnapshot()),
                "settings.set" => SettingsSet(args),
                "browser.applyExtensionFolder" => ApplyBrowser(args), // host→sidecar only

                // ── skills ───────────────────────────────────────────────────
                "skills.list" => Ok(await ReadSkillsAsync()),
                "skills.setEnabled" => await SkillsSetEnabledAsync(args),
                "skills.delete" => await SkillsDeleteAsync(args),
                "skills._install" => await SkillsInstallAsync(args), // host→sidecar only (path from native picker)

                // ── scheduled tasks (store + cron; firing is host-side) ──────
                "tasks.list" => Ok(new { tasks = (await _taskStore.GetAllAsync()).Select(TaskDto).ToArray() }),
                "tasks.create" => await TasksCreateAsync(args),
                "tasks.update" => await TasksUpdateAsync(args),
                "tasks.setEnabled" => await TasksSetEnabledAsync(args),
                "tasks.delete" => await TasksDeleteAsync(args),
                "tasks.runs" => Ok(new { runs = (await _taskStore.GetRunsAsync(Str(args, "taskId") ?? "", 50)).Select(RunDto).ToArray() }),
                "tasks._due" => Ok(await TasksDueAsync()),          // host→sidecar only
                "tasks._catchUp" => Ok(await TasksCatchUpAsync()),  // host→sidecar only (startup / wake)
                "tasks._recordRun" => await TasksRecordRunAsync(args), // host→sidecar only
                "tasks._recordSkip" => await TasksRecordSkipAsync(args), // host→sidecar only
                "tasks._reschedule" => await TasksRescheduleAsync(args), // host→sidecar only (A1.6 self-reschedule)

                // ── connectors (store + DPAPI; dialogs/oauth are host-side) ──
                "connectors.listCatalog" => Ok(new { connectors = (await _connectors.ListAvailableAsync()).Select(CatalogDto).ToArray() }),
                "connectors.listInstalled" => Ok(new { connectors = (await _connectors.GetInstalledAsync()).Select(InstalledDto).ToArray() }),
                "connectors.install" => await ConnectorsInstallAsync(args),
                "connectors.uninstall" => await ConnectorsUninstallAsync(args),
                "connectors._installApiKey" => await ConnectorsInstallApiKeyAsync(args), // host→sidecar only (key over the private pipe)
                "connectors._testCustom" => await ConnectorsTestCustomAsync(args),       // host→sidecar only
                "connectors._installCustom" => await ConnectorsInstallCustomAsync(args), // host→sidecar only

                // ── local global agent notes ─────────────────────────────────
                "globalNotes.list" => Ok(await GlobalNotesListAsync()),
                "globalNotes.delete" => await GlobalNotesDeleteAsync(args),
                "notes._forPrompt" => Ok(await NotesForPromptAsync(args)), // host→sidecar only (system-prompt recall)

                // FIX6: the origin a browser ACTION targets, resolved from the LIVE
                // tab (bridge tabs.list), for the host's per-site gate. host→sidecar
                // only. The site comes from the real tab, never a model arg.
                "browser._siteForAction" => await BrowserSiteForActionAsync(args),

                _ => (false, null, "unsupported"),
            };
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "host/query {Op} failed", op);
            return (false, null, "internal-error"); // coarse code only — never a host internal
        }
    }

    // ── settings ─────────────────────────────────────────────────────────────

    private object SettingsSnapshot()
    {
        var s = _settings.Current;
        return new
        {
            theme = s.Theme,
            minimizeToTray = s.MinimizeToTray,
            startWithWindows = s.StartWithWindows,
            crashReportingEnabled = s.CrashReportingEnabled,
            syncAgentConversations = s.SyncAgentConversations,
            preferLocalOfficeGeneration = s.PreferLocalOfficeGeneration,
            defaultModel = s.DefaultModel,
            chatEdgeBaseUrl = s.ChatEdgeBaseUrl,
            gatewayBaseUrl = s.GatewayBaseUrl,
            browserExtensionId = s.BrowserExtensionId,
            writingStyleProfile = s.WritingStyleProfile,
            locale = s.Locale,
            grantedFolders = s.GrantedFolders.ToArray(),
            // FIX25 §1b: the approval mode is a page-settable setting now (the
            // composer's selector). Normalised through the same parser the
            // sidecar's own run_command blocklist bypass uses, so page and tool
            // agree on what "skip" is.
            approvalMode = ApprovalModeParser.ToSettingsValue(ApprovalModeParser.Parse(s.ApprovalMode)),
            browser = _browserStatus(),
        };
    }

    private (bool, object?, string?) SettingsSet(JsonObject? args)
    {
        var key = Str(args, "key");
        var mapped = ParseSettingKey(key);
        if (mapped is null)
        {
            _logger.LogWarning("settings.set REFUSED: key '{Key}' not in the allow-list", key ?? "-");
            return (false, null, "refused");
        }
        if (!TryReadValue(mapped.Value, args, out var value))
            return Invalid();
        var restartRequired = ApplySetting(mapped.Value, value);
        return (true, new { key, restartRequired }, null);
    }

    private (bool, object?, string?) ApplyBrowser(JsonObject? args)
    {
        var path = Str(args, "path");
        return string.IsNullOrWhiteSpace(path) ? Invalid() : _applyExtensionFolder(path);
    }

    // ── skills ───────────────────────────────────────────────────────────────

    private async Task<object> ReadSkillsAsync()
    {
        var skills = await _skills.GetSkillsAsync();
        return new
        {
            skills = skills.Select(s => new
            {
                id = s.Manifest.Name,
                name = string.IsNullOrEmpty(s.Manifest.DisplayName) ? s.Manifest.Name : s.Manifest.DisplayName,
                description = s.Manifest.Description,
                triggers = s.Manifest.Triggers.Take(5).ToArray(),
                isBuiltin = s.IsBuiltin,
                isEnabled = s.IsEnabled,
            }).ToArray(),
        };
    }

    private async Task<(bool, object?, string?)> SkillsSetEnabledAsync(JsonObject? args)
    {
        var id = Str(args, "skillId");
        if (id is null || !TryBool(args, "enabled", out var enabled)) return Invalid();
        var skill = (await _skills.GetSkillsAsync()).FirstOrDefault(s => s.Manifest.Name == id);
        if (skill is null) return NotFound();
        await _skills.SetEnabledAsync(skill, enabled);
        return (true, new { skillId = id, enabled }, null);
    }

    private async Task<(bool, object?, string?)> SkillsDeleteAsync(JsonObject? args)
    {
        var id = Str(args, "skillId");
        if (id is null) return Invalid();
        var skill = (await _skills.GetSkillsAsync()).FirstOrDefault(s => s.Manifest.Name == id);
        if (skill is null) return NotFound();
        if (skill.IsBuiltin) return (false, null, "refused"); // mirrors WPF: builtins are not deletable
        await _skills.DeleteAsync(skill);
        return (true, new { skillId = id, deleted = true }, null);
    }

    private async Task<(bool, object?, string?)> SkillsInstallAsync(JsonObject? args)
    {
        var path = Str(args, "path");
        if (path is null) return Invalid();
        try
        {
            var skill = await _skills.InstallAsync(path);
            return (true, new { installed = true, name = skill.Manifest.Name }, null);
        }
        catch (Exception ex) when (ex is InvalidDataException or IOException)
        {
            return (false, null, "invalid");
        }
    }

    // ── scheduled tasks ────────────────────────────────────────────────────

    private async Task<(bool, object?, string?)> TasksCreateAsync(JsonObject? args)
    {
        var name = Str(args, "name"); var prompt = Str(args, "prompt"); var cron = Str(args, "cron");
        if (name is null || prompt is null || cron is null) return Invalid();
        if (CronHelper.NextOccurrence(cron) is null) return (false, null, "invalid"); // bad cron
        var task = new ScheduledTask
        {
            Name = Clamp(name, 200), Prompt = Clamp(prompt, 8000), CronExpression = cron,
            NextRunAt = CronHelper.NextOccurrence(cron),
            PermissionMode = NormalizeMode(Str(args, "permissionMode")),
        };
        await _taskStore.UpsertAsync(task);
        return (true, TaskDto(task), null);
    }

    private async Task<(bool, object?, string?)> TasksUpdateAsync(JsonObject? args)
    {
        var id = Str(args, "taskId"); var name = Str(args, "name"); var prompt = Str(args, "prompt"); var cron = Str(args, "cron");
        if (id is null || name is null || prompt is null || cron is null) return Invalid();
        var task = (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);
        if (task is null) return NotFound();
        if (CronHelper.NextOccurrence(cron) is null) return (false, null, "invalid");
        task.Name = Clamp(name, 200); task.Prompt = Clamp(prompt, 8000); task.CronExpression = cron;
        task.NextRunAt = CronHelper.NextOccurrence(cron);
        if (Str(args, "permissionMode") is { } pm) task.PermissionMode = NormalizeMode(pm);
        await _taskStore.UpsertAsync(task);
        return (true, TaskDto(task), null);
    }

    private static string NormalizeMode(string? mode) =>
        mode is "manual" or "auto" or "skip" ? mode : "manual";

    private async Task<(bool, object?, string?)> TasksSetEnabledAsync(JsonObject? args)
    {
        var id = Str(args, "taskId");
        if (id is null || !TryBool(args, "enabled", out var enabled)) return Invalid();
        var task = (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);
        if (task is null) return NotFound();
        task.IsEnabled = enabled;
        if (enabled && task.NextRunAt is null) task.NextRunAt = CronHelper.NextOccurrence(task.CronExpression);
        await _taskStore.UpsertAsync(task);
        return (true, TaskDto(task), null);
    }

    private async Task<(bool, object?, string?)> TasksDeleteAsync(JsonObject? args)
    {
        var id = Str(args, "taskId");
        if (id is null) return Invalid();
        var task = (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);
        if (task is null) return NotFound();
        await _taskStore.DeleteAsync(id);
        return (true, new { taskId = id, deleted = true }, null);
    }

    /// <summary>Host-only: the tasks due to run now — NextRunAt reached, plus the
    /// deterministic per-task stagger (A1.5), so tasks on the same minute do not all
    /// fire at once. The HOST runs each (a turn) and calls _recordRun/_recordSkip.</summary>
    private async Task<object> TasksDueAsync()
    {
        var now = DateTimeOffset.Now;
        var due = new List<ScheduledTask>();
        foreach (var task in (await _taskStore.GetAllAsync()).Where(t => t.IsEnabled))
        {
            if (task.NextRunAt is null)
            {
                task.NextRunAt = CronHelper.NextOccurrence(task.CronExpression);
                await _taskStore.UpsertAsync(task);
                continue;
            }
            if (task.NextRunAt.Value.AddSeconds(TaskStagger.SecondsFor(task.Id)) <= now) due.Add(task);
        }
        return new { tasks = due.Select(TaskDto).ToArray() };
    }

    /// <summary>Host-only (A1.1): missed-run catch-up, run ONCE on app start / wake.
    /// For each enabled task, find the occurrences it missed while the app was closed
    /// — STRICTLY within the last SEVEN DAYS. If any: record every older missed
    /// occurrence as a SKIP ("asleep"), keep exactly ONE catch-up for the MOST RECENT
    /// missed time, and advance NextRunAt to the next future occurrence. The task is
    /// returned to the host, which runs the single catch-up and notifies. A task with
    /// no missed occurrence in the window is not returned (nothing to catch up).</summary>
    private async Task<object> TasksCatchUpAsync()
    {
        var now = DateTimeOffset.Now;
        var caughtUp = new List<object>();
        foreach (var task in (await _taskStore.GetAllAsync()).Where(t => t.IsEnabled))
        {
            var (mostRecent, older) = TaskCatchUp.Compute(task.CronExpression, task.LastRunAt, now);
            if (mostRecent is null) continue;

            // Every occurrence OLDER than the most recent is discarded as a skip.
            foreach (var missed in older)
                await _taskStore.AppendRunAsync(new TaskRun { TaskId = task.Id, RanAt = now, ScheduledFor = missed, Outcome = "skipped", Reason = "asleep" });

            task.NextRunAt = CronHelper.NextOccurrence(task.CronExpression); // jump past the catch-up
            await _taskStore.UpsertAsync(task);
            caughtUp.Add(new
            {
                id = task.Id, name = task.Name, prompt = task.Prompt, conversationId = task.ConversationId,
                permissionMode = task.PermissionMode,
                catchUpFor = mostRecent, skippedOlder = older.Count,
            });
        }
        return new { tasks = caughtUp.ToArray() };
    }

    /// <summary>Host-only: record a run's outcome + a history line, and advance the
    /// schedule (mirrors TaskSchedulerService.RunNowAsync's finally block).</summary>
    private async Task<(bool, object?, string?)> TasksRecordRunAsync(JsonObject? args)
    {
        var id = Str(args, "taskId");
        if (id is null) return Invalid();
        var task = (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);
        if (task is null) return NotFound();
        var result = args?["result"]?.GetValue<string>();
        var conversationId = args?["conversationId"]?.GetValue<string>();
        var outcome = Str(args, "outcome") is "error" ? "error" : (Str(args, "outcome") is "catchup" ? "catchup" : "ok");
        var scheduledFor = args?["scheduledFor"]?.GetValue<string>();
        if (!string.IsNullOrEmpty(conversationId)) task.ConversationId = conversationId;
        if (result is not null) task.LastResult = Clamp(result, 500);
        task.LastRunAt = DateTimeOffset.Now;
        task.NextRunAt = CronHelper.NextOccurrence(task.CronExpression);
        await _taskStore.UpsertAsync(task);
        await _taskStore.AppendRunAsync(new TaskRun
        {
            TaskId = task.Id, RanAt = DateTimeOffset.Now,
            ScheduledFor = DateTimeOffset.TryParse(scheduledFor, out var sf) ? sf : null,
            Outcome = outcome, Result = result is null ? null : Clamp(result, 500),
        });
        return (true, TaskDto(task), null);
    }

    /// <summary>Host-only (A1.2): record that a due run was SKIPPED, with the reason
    /// the host observed ("prev_running" = this task's previous run was still going;
    /// "others_running" = other tasks were already running). Advances NextRunAt so
    /// the same occurrence is not retried forever.</summary>
    private async Task<(bool, object?, string?)> TasksRecordSkipAsync(JsonObject? args)
    {
        var id = Str(args, "taskId");
        if (id is null) return Invalid();
        var task = (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);
        if (task is null) return NotFound();
        var reason = Str(args, "reason") is "prev_running" or "others_running" or "asleep" ? Str(args, "reason") : "others_running";
        await _taskStore.AppendRunAsync(new TaskRun
        {
            TaskId = task.Id, RanAt = DateTimeOffset.Now, ScheduledFor = task.NextRunAt,
            Outcome = "skipped", Reason = reason,
        });
        task.NextRunAt = CronHelper.NextOccurrence(task.CronExpression);
        await _taskStore.UpsertAsync(task);
        return (true, TaskDto(task), null);
    }

    /// <summary>Host-only (A1.6): a task changed its OWN cron/prompt from inside its
    /// run. Either or both may be provided; a bad cron is refused ("invalid").</summary>
    private async Task<(bool, object?, string?)> TasksRescheduleAsync(JsonObject? args)
    {
        var id = Str(args, "taskId");
        if (id is null) return Invalid();
        var task = (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);
        if (task is null) return NotFound();
        var cron = Str(args, "cron");
        var prompt = Str(args, "prompt");
        if (cron is not null)
        {
            if (CronHelper.NextOccurrence(cron) is null) return (false, null, "invalid");
            task.CronExpression = cron;
            task.NextRunAt = CronHelper.NextOccurrence(cron);
        }
        if (prompt is not null) task.Prompt = Clamp(prompt, 8000);
        await _taskStore.UpsertAsync(task);
        return (true, TaskDto(task), null);
    }

    // ── connectors ───────────────────────────────────────────────────────────

    private async Task<(bool, object?, string?)> ConnectorsInstallAsync(JsonObject? args)
    {
        var id = Str(args, "connectorId");
        if (id is null) return Invalid();
        var connector = (await _connectors.ListAvailableAsync()).FirstOrDefault(c => c.Id == id);
        if (connector is null) return NotFound();
        if (connector.AuthType == "oauth2")
            return (false, null, "oauth-unsupported"); // §1.3 — host answers legibly; never installed silently
        if (connector.AuthType == "api_key")
            // The host must collect the key NATIVELY, then call _installApiKey.
            return (false, new { needsApiKey = true, name = connector.Name }, "needs-api-key");
        await _connectors.InstallAsync(id, null);
        return (true, new { connectorId = id, installed = true }, null);
    }

    private async Task<(bool, object?, string?)> ConnectorsInstallApiKeyAsync(JsonObject? args)
    {
        var id = Str(args, "connectorId");
        var token = args?["apiToken"]?.GetValue<string>(); // over the private stdio pipe; never logged, never to the page
        if (id is null || string.IsNullOrWhiteSpace(token)) return Invalid();
        await _connectors.InstallAsync(id, token);
        return (true, new { connectorId = id, installed = true }, null);
    }

    private async Task<(bool, object?, string?)> ConnectorsUninstallAsync(JsonObject? args)
    {
        var id = Str(args, "connectorId");
        if (id is null) return Invalid();
        await _connectors.UninstallAsync(id);
        return (true, new { connectorId = id, installed = false }, null);
    }

    private async Task<(bool, object?, string?)> ConnectorsTestCustomAsync(JsonObject? args)
    {
        var connector = ParseCustomConnector(args, out var token);
        if (connector is null) return Invalid();
        var result = await _connectors.TestConnectionAsync(connector, token);
        return (true, new { ok = result.Success, toolCount = result.ToolCount, error = result.Error }, null);
    }

    private async Task<(bool, object?, string?)> ConnectorsInstallCustomAsync(JsonObject? args)
    {
        var connector = ParseCustomConnector(args, out var token);
        if (connector is null) return Invalid();
        try
        {
            await _connectors.InstallCustomAsync(connector, token);
            return (true, new { connectorId = connector.Id, installed = true }, null);
        }
        catch (ArgumentException)
        {
            return (false, null, "invalid"); // bad definition or duplicate id
        }
    }

    // ── global notes ─────────────────────────────────────────────────────────

    private async Task<object> GlobalNotesListAsync()
    {
        var notes = await _store.GetGlobalNotesAsync();
        return new { notes = notes.Reverse().Select(n => new { id = n.Id, content = n.Content, createdAt = n.CreatedAt }).ToArray() };
    }

    private async Task<(bool, object?, string?)> GlobalNotesDeleteAsync(JsonObject? args)
    {
        var id = Str(args, "noteId");
        if (id is null) return Invalid();
        var notes = await _store.GetGlobalNotesAsync();
        if (notes.All(n => n.Id != id)) return NotFound();
        await _store.DeleteNoteAsync(id);
        return (true, new { noteId = id, deleted = true }, null);
    }

    /// <summary>Host-only (FIX5 §1): the LOCAL durable notes for a turn's system
    /// prompt — the conversation-scoped notes and the global notes the
    /// <c>remember</c> tool writes. Mirrors the two reads in
    /// <c>AgentTurnAssembler.AppendNotesAsync</c> so the Electron host can render
    /// the SAME two sections the WPF app does (<c>## Memory (global)</c> +
    /// <c>## Notes (this conversation)</c>). The host merges the global half with
    /// chat-edge's server memories and does the rendering — it holds the JWT; the
    /// sidecar never does. Before FIX5 the Electron path skipped the local store
    /// entirely, so <c>remember</c> saved but was never recalled.</summary>
    private async Task<object> NotesForPromptAsync(JsonObject? args)
    {
        var conversationId = Str(args, "conversationId");
        IReadOnlyList<AgentNote> conversationNotes = conversationId is not null
            ? await _store.GetNotesAsync(conversationId)
            : Array.Empty<AgentNote>();
        var globalNotes = await _store.GetGlobalNotesAsync();
        return new
        {
            conversationNotes = conversationNotes.Select(NoteDto).ToArray(),
            globalNotes = globalNotes.Select(NoteDto).ToArray(),
        };
    }

    private static object NoteDto(AgentNote n) => new { content = n.Content, createdAt = n.CreatedAt };

    /// <summary>Host-only (FIX6): the origin a browser action targets, from the LIVE
    /// tab. tabId picks the tab (or the active one when absent); the URL comes from
    /// the bridge's tabs.list, so the SITE is never a value the model supplied — it
    /// can only choose WHICH real tab. Returns {url, site} or an error the host
    /// turns into a legible refusal ("could not determine the site").</summary>
    private async Task<(bool, object?, string?)> BrowserSiteForActionAsync(JsonObject? args)
    {
        int? tabId = null;
        if (args?["tabId"] is JsonValue v && v.TryGetValue<int>(out var id)) tabId = id;
        var (ok, url, site) = await _browserSiteResolver(tabId);
        if (!ok || string.IsNullOrEmpty(site))
            return (false, null, "no active tab, or the browser bridge is not connected");
        return (true, new { url, site }, null);
    }

    // ── DTOs (ported verbatim from ShellHostQueryCoordinator) ─────────────────

    private static object TaskDto(ScheduledTask t) => new
    {
        id = t.Id, name = t.Name, prompt = t.Prompt, cron = t.CronExpression,
        isEnabled = t.IsEnabled, nextRunAt = t.NextRunAt, lastRunAt = t.LastRunAt, lastResult = t.LastResult,
        permissionMode = t.PermissionMode, staggerSeconds = TaskStagger.SecondsFor(t.Id),
    };

    private static object RunDto(TaskRun r) => new
    {
        id = r.Id, ranAt = r.RanAt, scheduledFor = r.ScheduledFor,
        outcome = r.Outcome, reason = r.Reason, result = r.Result,
    };

    private object CatalogDto(McpConnector c) => new
    {
        id = c.Id, name = c.Name, description = c.Description, category = c.Category,
        authType = c.AuthType, tools = c.Tools.ToArray(),
        installed = _connectors.IsInstalled(c.Id),
        status = _connectors.GetStatus(c.Id).ToString(),
    };

    private object InstalledDto(InstalledConnector r) => new
    {
        id = r.Id,
        isCustom = r.Custom is not null,
        name = r.Custom?.Name,
        tools = _connectors.GetCachedToolNames(r.Id).ToArray(),
        status = _connectors.GetStatus(r.Id).ToString(),
    };

    private static McpConnector? ParseCustomConnector(JsonObject? args, out string? apiToken)
    {
        apiToken = args?["apiToken"]?.GetValue<string>();
        var def = args?["connector"] as JsonObject;
        if (def is null) return null;
        var id = def["id"]?.GetValue<string>();
        var name = def["name"]?.GetValue<string>();
        var command = def["command"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(command))
            return null;
        var argList = (def["args"] as JsonArray)?.Select(a => a?.GetValue<string>() ?? "").Where(a => a.Length > 0).ToList() ?? new();
        return new McpConnector
        {
            Id = id!, Name = name!, Description = "Custom stdio connector", Category = "developer",
            AuthType = string.IsNullOrWhiteSpace(apiToken) ? "none" : "api_key",
            Transport = McpConnector.TransportStdio, Command = command, Args = argList,
        };
    }

    // ── the CLOSED settings allow-list (mirrors HostQueryProtocol) ────────────

    private enum Key
    {
        Theme, MinimizeToTray, StartWithWindows, CrashReportingEnabled,
        SyncAgentConversations, PreferLocalOfficeGeneration, DefaultModel,
        ChatEdgeBaseUrl, GatewayBaseUrl, BrowserExtensionId, WritingStyleProfile, Locale,
        ApprovalMode, // FIX25 §1b — manual | auto | skip; the host reads it live per tool call
    }

    private static Key? ParseSettingKey(string? key) => key switch
    {
        "theme" => Key.Theme,
        "minimizeToTray" => Key.MinimizeToTray,
        "startWithWindows" => Key.StartWithWindows,
        "crashReportingEnabled" => Key.CrashReportingEnabled,
        "syncAgentConversations" => Key.SyncAgentConversations,
        "preferLocalOfficeGeneration" => Key.PreferLocalOfficeGeneration,
        "defaultModel" => Key.DefaultModel,
        "chatEdgeBaseUrl" => Key.ChatEdgeBaseUrl,
        "gatewayBaseUrl" => Key.GatewayBaseUrl,
        "browserExtensionId" => Key.BrowserExtensionId,
        "writingStyleProfile" => Key.WritingStyleProfile,
        "locale" => Key.Locale,
        "approvalMode" => Key.ApprovalMode,
        _ => null,
    };

    private static bool TryReadValue(Key key, JsonObject? args, out object? value)
    {
        value = null;
        if (args is null || !args.TryGetPropertyValue("value", out var vNode) || vNode is not JsonValue v)
            return false;
        switch (key)
        {
            case Key.MinimizeToTray:
            case Key.StartWithWindows:
            case Key.CrashReportingEnabled:
            case Key.SyncAgentConversations:
            case Key.PreferLocalOfficeGeneration:
                if (!v.TryGetValue<bool>(out var b)) return false;
                value = b; return true;
            case Key.Theme:
                if (!v.TryGetValue<string>(out var theme) || theme is not ("Dark" or "Light" or "System")) return false;
                value = theme; return true;
            case Key.ChatEdgeBaseUrl:
            case Key.GatewayBaseUrl:
                if (!v.TryGetValue<string>(out var url)) return false;
                url = url.Trim();
                if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https")) return false;
                value = url.TrimEnd('/'); return true;
            case Key.DefaultModel:
            case Key.BrowserExtensionId:
            case Key.Locale:
                if (!v.TryGetValue<string>(out var s) || s.Length > 256) return false;
                value = s; return true;
            case Key.WritingStyleProfile:
                if (!v.TryGetValue<string>(out var profile) || profile.Length > 8000) return false;
                value = profile; return true;
            case Key.ApprovalMode:
                // Closed vocabulary — the page cannot invent a fourth mode, and the
                // parser's "unknown → auto" fallback never gets to launder one.
                if (!v.TryGetValue<string>(out var mode) || mode is not ("manual" or "auto" or "skip")) return false;
                value = mode; return true;
            default:
                return false;
        }
    }

    private bool ApplySetting(Key key, object? value)
    {
        var updated = _settings.Current.Clone();
        var restartRequired = false;
        switch (key)
        {
            case Key.Theme: updated.Theme = (string)value!; break;
            case Key.MinimizeToTray: updated.MinimizeToTray = (bool)value!; break;
            case Key.StartWithWindows: updated.StartWithWindows = (bool)value!; break; // host applies the login-item effect
            case Key.CrashReportingEnabled: updated.CrashReportingEnabled = (bool)value!; break;
            case Key.SyncAgentConversations: updated.SyncAgentConversations = (bool)value!; break;
            case Key.PreferLocalOfficeGeneration: updated.PreferLocalOfficeGeneration = (bool)value!; break;
            case Key.DefaultModel: updated.DefaultModel = (string)value!; break;
            case Key.BrowserExtensionId: updated.BrowserExtensionId = (string)value!; break;
            case Key.WritingStyleProfile: updated.WritingStyleProfile = (string)value!; break;
            case Key.Locale: updated.Locale = (string)value!; break;
            case Key.ApprovalMode: updated.ApprovalMode = (string)value!; break; // FIX25 — the store of record; the host reads it per tool call
            case Key.ChatEdgeBaseUrl:
                restartRequired = updated.ChatEdgeBaseUrl != (string)value!;
                updated.ChatEdgeBaseUrl = (string)value!;
                break;
            case Key.GatewayBaseUrl:
                restartRequired = updated.GatewayBaseUrl != (string)value!;
                updated.GatewayBaseUrl = (string)value!;
                break;
        }
        _settings.Save(updated);
        return restartRequired;
    }

    // ── helpers ────────────────────────────────────────────────────────────

    private static (bool, object?, string?) Ok(object? data) => (true, data, null);
    private static (bool, object?, string?) Invalid() => (false, null, "invalid");
    private static (bool, object?, string?) NotFound() => (false, null, "not-found");
    private static string Clamp(string s, int max) => s.Length <= max ? s : s[..max];

    private static string? Str(JsonObject? args, string name)
    {
        if (args is null || !args.TryGetPropertyValue(name, out var n) || n is not JsonValue v) return null;
        return v.TryGetValue<string>(out var s) && s.Length is > 0 and <= 8000 ? s : null;
    }

    private static bool TryBool(JsonObject? args, string name, out bool value)
    {
        value = false;
        if (args is null || !args.TryGetPropertyValue(name, out var n) || n is not JsonValue v) return false;
        return v.TryGetValue<bool>(out value);
    }
}
