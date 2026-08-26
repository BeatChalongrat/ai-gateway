using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Shell.Seams;

namespace SranDesktop.Shell.Bridge;

/// <summary>
/// Phase 5 §2 — the host side of the control vocabulary. Subscribes to
/// <see cref="BridgeChannel.HostQueryReceived"/>, maps each closed
/// <see cref="HostQueryOp"/> to a real Application service, applies the
/// per-op refusal rules (§2.3), and posts exactly one <c>host.result</c>.
///
/// The dangerous operations never take a sensitive value from the page: skill
/// install and folder grant open a NATIVE OS picker (<see cref="IShellNativeDialogs"/>),
/// api-key install prompts NATIVELY, and add-custom-connector runs the NATIVE
/// spawn-consent flow. The page's vocabulary contains no path, no command line,
/// no secret — the same property the turn protocol has.
/// </summary>
public sealed class ShellHostQueryCoordinator
{
    private readonly ISkillManager _skills;
    private readonly IScheduledTaskStore _taskStore;
    private readonly ITaskSchedulerService _scheduler;
    private readonly IMcpConnectorService _connectors;
    private readonly ConnectorOAuthCoordinator _oauth;
    private readonly ISettingsService _settings;
    private readonly IStartupManager _startup;
    private readonly IConversationStore _store;
    private readonly ICrashReportService _crash;
    private readonly IShellNativeDialogs _native;
    private readonly ILogger<ShellHostQueryCoordinator> _logger;

    private BridgeChannel? _bridge;

    // run_now debounce so the page cannot spam scheduled agent runs (§2.3).
    private readonly HashSet<string> _running = new();
    private readonly object _runningGate = new();

    public ShellHostQueryCoordinator(
        ISkillManager skills,
        IScheduledTaskStore taskStore,
        ITaskSchedulerService scheduler,
        IMcpConnectorService connectors,
        ConnectorOAuthCoordinator oauth,
        ISettingsService settings,
        IStartupManager startup,
        IConversationStore store,
        ICrashReportService crash,
        IShellNativeDialogs native,
        ILogger<ShellHostQueryCoordinator> logger)
    {
        _skills = skills;
        _taskStore = taskStore;
        _scheduler = scheduler;
        _connectors = connectors;
        _oauth = oauth;
        _settings = settings;
        _startup = startup;
        _store = store;
        _crash = crash;
        _native = native;
        _logger = logger;
    }

    public void Attach(BridgeChannel bridge)
    {
        _bridge = bridge;
        bridge.HostQueryReceived += OnHostQuery;
    }

    private void OnHostQuery(HostQuery query)
    {
        // Fire-and-forget: each op is independent and posts its own result.
        _ = Task.Run(() => DispatchAsync(query));
    }

    private async Task DispatchAsync(HostQuery q)
    {
        try
        {
            var (ok, data, error) = await RouteAsync(q);
            _bridge?.PostHostResult(q.RequestId, ok, data, error);
            _logger.LogInformation("host.query {Op} (req={Req}) → ok={Ok} error={Error}",
                q.Op, q.RequestId, ok, error ?? "-");
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "host.query {Op} failed (req={Req})", q.Op, q.RequestId);
            // Coarse code only — never a host internal (§2.2).
            _bridge?.PostHostResult(q.RequestId, false, null, "internal-error");
        }
    }

    internal async Task<(bool ok, object? data, string? error)> RouteAsync(HostQuery q)
    {
        switch (q.Op)
        {
            // ── skills ────────────────────────────────────────────────────
            case HostQueryOp.SkillsList:
                return Ok(await ReadSkillsAsync());

            case HostQueryOp.SkillsSetEnabled:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "skillId", out var id)
                    || !HostQueryProtocol.TryBoolArg(q.Args, "enabled", out var enabled))
                    return Invalid();
                var skill = await ResolveSkillAsync(id);
                if (skill is null) return NotFound();
                await _skills.SetEnabledAsync(skill, enabled);
                return Ok(new { skillId = id, enabled });
            }

            case HostQueryOp.SkillsInstallPicker:
            {
                // NATIVE picker — no path from the page (§2.3/§2.4).
                var path = await _native.PickSkillPackageAsync();
                if (path is null) return Ok(new { installed = false, cancelled = true });
                try
                {
                    var skill = await _skills.InstallAsync(path);
                    return Ok(new { installed = true, name = skill.Manifest.Name });
                }
                catch (Exception ex) when (ex is InvalidDataException or IOException)
                {
                    return (false, null, "invalid");
                }
            }

            case HostQueryOp.SkillsDelete:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "skillId", out var id)) return Invalid();
                var skill = await ResolveSkillAsync(id);
                if (skill is null) return NotFound();
                if (skill.IsBuiltin) return (false, null, "refused"); // mirrors WPF: builtins are not deletable
                await _skills.DeleteAsync(skill);
                return Ok(new { skillId = id, deleted = true });
            }

            // ── scheduled tasks ──────────────────────────────────────────
            case HostQueryOp.TasksList:
                return Ok(new { tasks = (await _taskStore.GetAllAsync()).Select(TaskDto).ToArray() });

            case HostQueryOp.TasksCreate:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "name", out var name)
                    || !HostQueryProtocol.TryStringArg(q.Args, "prompt", out var prompt)
                    || !HostQueryProtocol.TryStringArg(q.Args, "cron", out var cron))
                    return Invalid();
                if (_scheduler.NextOccurrence(cron) is null) return (false, null, "invalid"); // bad cron
                var task = new ScheduledTask
                {
                    Name = Clamp(name, 200), Prompt = Clamp(prompt, 8000), CronExpression = cron,
                    NextRunAt = _scheduler.NextOccurrence(cron),
                };
                await _taskStore.UpsertAsync(task);
                return Ok(TaskDto(task));
            }

            case HostQueryOp.TasksUpdate:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "taskId", out var id)
                    || !HostQueryProtocol.TryStringArg(q.Args, "name", out var name)
                    || !HostQueryProtocol.TryStringArg(q.Args, "prompt", out var prompt)
                    || !HostQueryProtocol.TryStringArg(q.Args, "cron", out var cron))
                    return Invalid();
                var task = await ResolveTaskAsync(id);
                if (task is null) return NotFound();
                if (_scheduler.NextOccurrence(cron) is null) return (false, null, "invalid");
                task.Name = Clamp(name, 200);
                task.Prompt = Clamp(prompt, 8000);
                task.CronExpression = cron;
                task.NextRunAt = _scheduler.NextOccurrence(cron);
                await _taskStore.UpsertAsync(task);
                return Ok(TaskDto(task));
            }

            case HostQueryOp.TasksSetEnabled:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "taskId", out var id)
                    || !HostQueryProtocol.TryBoolArg(q.Args, "enabled", out var enabled))
                    return Invalid();
                var task = await ResolveTaskAsync(id);
                if (task is null) return NotFound();
                task.IsEnabled = enabled;
                if (enabled && task.NextRunAt is null)
                    task.NextRunAt = _scheduler.NextOccurrence(task.CronExpression);
                await _taskStore.UpsertAsync(task);
                return Ok(TaskDto(task));
            }

            case HostQueryOp.TasksDelete:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "taskId", out var id)) return Invalid();
                var task = await ResolveTaskAsync(id);
                if (task is null) return NotFound();
                await _taskStore.DeleteAsync(id);
                return Ok(new { taskId = id, deleted = true });
            }

            case HostQueryOp.TasksRunNow:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "taskId", out var id)) return Invalid();
                var task = await ResolveTaskAsync(id);
                if (task is null) return NotFound();
                lock (_runningGate)
                {
                    if (!_running.Add(id)) return (false, null, "refused"); // already running — debounce
                }
                try
                {
                    await _scheduler.RunNowAsync(task);
                    return Ok(TaskDto(task));
                }
                finally
                {
                    lock (_runningGate) _running.Remove(id);
                }
            }

            // ── connectors ───────────────────────────────────────────────
            case HostQueryOp.ConnectorsListCatalog:
                return Ok(new { connectors = (await _connectors.ListAvailableAsync()).Select(CatalogDto).ToArray() });

            case HostQueryOp.ConnectorsListInstalled:
                return Ok(new { connectors = (await _connectors.GetInstalledAsync()).Select(InstalledDto).ToArray() });

            case HostQueryOp.ConnectorsInstall:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "connectorId", out var id)) return Invalid();
                var connector = (await _connectors.ListAvailableAsync()).FirstOrDefault(c => c.Id == id);
                if (connector is null) return NotFound();
                if (connector.AuthType == "oauth2")
                    return (false, null, "refused"); // oauth2 must go through oauthConnect (system browser)
                string? token = null;
                if (connector.AuthType == "api_key")
                {
                    // NATIVE secret prompt — the key never crosses the bridge (§2.4).
                    token = await _native.PromptApiKeyAsync(connector.Name);
                    if (string.IsNullOrWhiteSpace(token)) return Ok(new { installed = false, cancelled = true });
                }
                await _connectors.InstallAsync(id, token);
                return Ok(new { connectorId = id, installed = true });
            }

            case HostQueryOp.ConnectorsUninstall:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "connectorId", out var id)) return Invalid();
                await _connectors.UninstallAsync(id);
                return Ok(new { connectorId = id, installed = false });
            }

            case HostQueryOp.ConnectorsOauthConnect:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "connectorId", out var id)) return Invalid();
                await _oauth.StartConnectAsync(id); // system browser broker (host-driven)
                return Ok(new { connectorId = id, started = true });
            }

            case HostQueryOp.ConnectorsOauthDisconnect:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "connectorId", out var id)) return Invalid();
                await _oauth.DisconnectAsync(id);
                return Ok(new { connectorId = id, installed = false });
            }

            case HostQueryOp.ConnectorsAddCustomDialog:
            {
                // Entirely NATIVE: the user authors the command line and approves
                // the spawn in a native window; the page names no process (§2.4).
                var added = await _native.ShowAddCustomConnectorAsync();
                return Ok(new { added });
            }

            // ── settings ─────────────────────────────────────────────────
            case HostQueryOp.SettingsGet:
                return Ok(SettingsSnapshot());

            case HostQueryOp.SettingsSet:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "key", out var keyString)) return Invalid();
                var key = HostQueryProtocol.ParseSettingKey(keyString);
                if (key is null)
                {
                    _logger.LogWarning("settings.set REFUSED: key '{Key}' not in the allow-list", keyString);
                    return (false, null, "refused"); // §2.3 — dropped and logged
                }
                if (!HostQueryProtocol.TryReadSettingValue(key.Value, q.Args, out var value))
                    return Invalid();
                var restartRequired = ApplySetting(key.Value, value);
                return Ok(new { key = keyString, restartRequired });
            }

            // ── folders (native picker; page never names a grant path) ───
            case HostQueryOp.FoldersList:
                return Ok(new { folders = _settings.Current.GrantedFolders.ToArray() });

            case HostQueryOp.FoldersPickAndGrant:
            {
                // NO path argument (§2.3). The native picker returns a path the
                // USER chose; the validator refuses drive/system roots.
                var picked = await _native.PickFolderAsync();
                if (picked is null) return Ok(new { granted = false, cancelled = true });
                var rejection = FolderGrantValidator.Validate(picked, out var canonical);
                if (rejection != FolderGrantRejection.Ok || canonical is null)
                {
                    _logger.LogWarning("folders.pickAndGrant REFUSED: {Rejection}", rejection);
                    return (false, null, "refused");
                }
                var updated = _settings.Current.Clone();
                if (updated.GrantedFolders.Any(f => string.Equals(f, canonical, StringComparison.OrdinalIgnoreCase)))
                    return Ok(new { granted = false, duplicate = true, folders = updated.GrantedFolders.ToArray() });
                updated.GrantedFolders.Add(canonical);
                _settings.Save(updated);
                return Ok(new { granted = true, folders = updated.GrantedFolders.ToArray() });
            }

            case HostQueryOp.FoldersRevoke:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "folder", out var folder)) return Invalid();
                var updated = _settings.Current.Clone();
                // Honour ONLY an exact match of a currently-granted path (§2.3):
                // no filesystem oracle by revoke-error.
                var match = updated.GrantedFolders.FirstOrDefault(
                    f => string.Equals(f, folder, StringComparison.OrdinalIgnoreCase));
                if (match is null) return NotFound();
                updated.GrantedFolders.Remove(match);
                _settings.Save(updated);
                return Ok(new { revoked = true, folders = updated.GrantedFolders.ToArray() });
            }

            // ── local global agent notes ─────────────────────────────────
            case HostQueryOp.GlobalNotesList:
            {
                var notes = await _store.GetGlobalNotesAsync();
                return Ok(new
                {
                    notes = notes.Reverse().Select(n => new { id = n.Id, content = n.Content, createdAt = n.CreatedAt }).ToArray(),
                });
            }

            case HostQueryOp.GlobalNotesDelete:
            {
                if (!HostQueryProtocol.TryStringArg(q.Args, "noteId", out var id)) return Invalid();
                var notes = await _store.GetGlobalNotesAsync();
                if (notes.All(n => n.Id != id)) return NotFound();
                await _store.DeleteNoteAsync(id);
                return Ok(new { noteId = id, deleted = true });
            }

            // ── renderer error reporting (§4.2) ──────────────────────────
            case HostQueryOp.DiagReportRendererError:
            {
                HostQueryProtocol.TryStringArg(q.Args, "message", out var message);
                HostQueryProtocol.TryStringArg(q.Args, "kind", out var kind);
                var stack = q.Args.ValueKind == JsonValueKind.Object
                            && q.Args.TryGetProperty("stack", out var se) && se.ValueKind == JsonValueKind.String
                    ? se.GetString() : null;
                var accepted = await _crash.ReportAsync(new RendererException(kind, message, stack));
                _logger.LogWarning("Renderer error reported (kind={Kind}): {Message}", kind, Clamp(message, 200));
                return Ok(new { accepted });
            }

            default:
                return (false, null, "unknown-op");
        }
    }

    // ── helpers ──────────────────────────────────────────────────────────

    private static (bool, object?, string?) Ok(object? data) => (true, data, null);
    private static (bool, object?, string?) Invalid() => (false, null, "invalid");
    private static (bool, object?, string?) NotFound() => (false, null, "not-found");

    private static string Clamp(string s, int max) => s.Length <= max ? s : s[..max];

    private async Task<Skill?> ResolveSkillAsync(string id) =>
        (await _skills.GetSkillsAsync()).FirstOrDefault(s => s.Manifest.Name == id);

    private async Task<ScheduledTask?> ResolveTaskAsync(string id) =>
        (await _taskStore.GetAllAsync()).FirstOrDefault(t => t.Id == id);

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

    private static object TaskDto(ScheduledTask t) => new
    {
        id = t.Id, name = t.Name, prompt = t.Prompt, cron = t.CronExpression,
        isEnabled = t.IsEnabled, nextRunAt = t.NextRunAt, lastRunAt = t.LastRunAt, lastResult = t.LastResult,
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

    private object SettingsSnapshot()
    {
        var s = _settings.Current;
        // No secret ever leaves the host: no DPAPI token, no Azure key, no pairing token.
        return new
        {
            theme = s.Theme,
            minimizeToTray = s.MinimizeToTray,
            startWithWindows = _startup.IsEnabled,
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
        };
    }

    /// <summary>Applies one validated setting; returns whether an endpoint change
    /// requires a restart (mirrors the WPF Settings Save semantics).</summary>
    private bool ApplySetting(SettingKey key, object? value)
    {
        var updated = _settings.Current.Clone();
        var restartRequired = false;
        switch (key)
        {
            case SettingKey.Theme: updated.Theme = (string)value!; break;
            case SettingKey.MinimizeToTray: updated.MinimizeToTray = (bool)value!; break;
            case SettingKey.StartWithWindows:
                updated.StartWithWindows = (bool)value!;
                try { _startup.Set((bool)value!); }
                catch (Exception ex) { _logger.LogWarning(ex, "auto-start registry write failed"); }
                break;
            case SettingKey.CrashReportingEnabled: updated.CrashReportingEnabled = (bool)value!; break;
            case SettingKey.SyncAgentConversations: updated.SyncAgentConversations = (bool)value!; break;
            case SettingKey.PreferLocalOfficeGeneration: updated.PreferLocalOfficeGeneration = (bool)value!; break;
            case SettingKey.DefaultModel: updated.DefaultModel = (string)value!; break;
            case SettingKey.BrowserExtensionId: updated.BrowserExtensionId = (string)value!; break;
            case SettingKey.WritingStyleProfile: updated.WritingStyleProfile = (string)value!; break;
            case SettingKey.Locale: updated.Locale = (string)value!; break;
            case SettingKey.ChatEdgeBaseUrl:
                restartRequired = updated.ChatEdgeBaseUrl != (string)value!;
                updated.ChatEdgeBaseUrl = (string)value!;
                break;
            case SettingKey.GatewayBaseUrl:
                restartRequired = updated.GatewayBaseUrl != (string)value!;
                updated.GatewayBaseUrl = (string)value!;
                break;
        }
        _settings.Save(updated);
        return restartRequired;
    }

    /// <summary>Carries a renderer-side (JS) failure into CrashReportService,
    /// which is shaped around an <see cref="Exception"/>. The stack is the page's
    /// own; nothing is executed.</summary>
    private sealed class RendererException : Exception
    {
        private readonly string? _stack;
        public RendererException(string? kind, string? message, string? stack)
            : base($"[renderer:{kind ?? "error"}] {message}") => _stack = stack;
        public override string? StackTrace => _stack;
    }
}
