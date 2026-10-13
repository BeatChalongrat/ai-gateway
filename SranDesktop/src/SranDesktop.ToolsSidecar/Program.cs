using System.Text;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Browser;
using SranDesktop.Infrastructure.Platform;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.ToolsSidecar;

// ---------------------------------------------------------------------------
// SranDesktop.ToolsSidecar — E4: the full tool set, minus UIA (owner decision:
// not registered; the code stays in Infrastructure.Windows.Desktop, which this
// exe does not reference) and minus the clipboard (the Electron host's).
//
// Host-side by design (the E4 record, §1): run_subagent, web_search,
// run_python, read_clipboard, write_clipboard. Everything else is here.
//
// stdout is the MCP wire and nothing else; every log line goes to stderr.
// State lives under SRAN_SIDECAR_DATA_DIR (the Electron app's data folder),
// never in the WPF app's %APPDATA%\SRAN Desktop — the two hosts must not
// share a database, a credential file, or a pairing token.
// ---------------------------------------------------------------------------

var utf8 = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
var wireOut = new StreamWriter(Console.OpenStandardOutput(), utf8) { AutoFlush = false };
var wireIn = new StreamReader(Console.OpenStandardInput(), utf8);
Console.SetOut(Console.Error);

using var loggerFactory = LoggerFactory.Create(b => b.AddProvider(new StderrLoggerProvider(LogLevel.Information)).SetMinimumLevel(LogLevel.Information));
var logger = loggerFactory.CreateLogger("Sidecar");

var dataDir = Environment.GetEnvironmentVariable("SRAN_SIDECAR_DATA_DIR");
if (string.IsNullOrWhiteSpace(dataDir) || !Path.IsPathFullyQualified(dataDir))
{
    logger.LogError("SRAN_SIDECAR_DATA_DIR must be an absolute path (the host's data folder). Refusing to start.");
    return 2;
}
Directory.CreateDirectory(dataDir);
logger.LogInformation("SranDesktop.ToolsSidecar {Version} starting, pid {Pid}, data={Data}, base={Base}",
    SidecarInfo.Version, Environment.ProcessId, dataDir, AppContext.BaseDirectory);

// The sidecar's own state is off-limits to the file tools, like the WPF app's.
PathGuard.ConfigureDeniedRoots(new[] { dataDir });

// ── stores and services ──────────────────────────────────────────────────────
var credentials = new DpapiCredentialStore(Path.Combine(dataDir, "credentials.dat"), loggerFactory.CreateLogger<DpapiCredentialStore>());
var store = new SqliteConversationStore(Path.Combine(dataDir, "data.db"));
await store.InitializeAsync();
// FIX2: the scheduled-task store (the firing timer lives in the host — §1.1).
var taskStore = new SqliteScheduledTaskStore(Path.Combine(dataDir, "tasks.db"));
var session = new ShellSession();
var skills = new SkillManager(Path.Combine(dataDir, "skills"), loggerFactory.CreateLogger<SkillManager>());
var launcher = new SystemBrowserLauncher(loggerFactory.CreateLogger<SystemBrowserLauncher>());

// FIX1: the sidecar's own settings store (its data dir) — backs the host-only
// settings.get/settings.set surface (§1.3) and holds the browser config.
var settings = new JsonSettingsService(new UserSettings(), Path.Combine(dataDir, "settings.json"), loggerFactory.CreateLogger<JsonSettingsService>());

// FIX1 (collisions #1/#2/#3): the Electron app's DISTINCT browser identity, so
// it never collides with the WPF app while both run. A distinct native-messaging
// host name (⇒ distinct registry key + manifest name), a distinct pipe (the tag
// folds into BrowserWire.PipeName's hash), and the deployed bundle's HOST is
// rewritten to match. The WPF app keeps every default and is unchanged.
const string ElectronHostName = "com.sran.browser.electron";
const string ElectronPipeTag = "electron";

// web_fetch's named client — the same options AddSranCore configures.
var services = new ServiceCollection();
services.AddHttpClient(WebFetchTool.HttpClientName, client => client.Timeout = TimeSpan.FromSeconds(30))
    .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler { AllowAutoRedirect = false, AutomaticDecompression = System.Net.DecompressionMethods.All });
var httpFactory = services.BuildServiceProvider().GetRequiredService<IHttpClientFactory>();

// ── browser bridge: pipe server + relay manifest + pairing (E4 §2) ───────────
// The extension bundle ships beside the sidecar and is DEPLOYED into the data
// folder; pairing.json is written there, never into the repo tree. The
// unpacked extension's id is derived from that folder, so the manifest's
// allowed_origins names the deployed copy.
// Distinct pipe (collision #1): the tag folds into the hash; the relay reads the
// same name from `browser-host.pipe` beside its exe (written below).
var electronPipeName = BrowserWire.PipeName(ElectronPipeTag);
var bridge = new BrowserBridgeService(credentials, loggerFactory.CreateLogger<BrowserBridgeService>(), pipeName: electronPipeName);
var deployedExtension = Path.Combine(dataDir, "browser-extension");
var relayExe = Path.Combine(AppContext.BaseDirectory, "SranDesktop.BrowserHost.exe");
var installer = new BrowserExtensionInstaller(
    loggerFactory.CreateLogger<BrowserExtensionInstaller>(),
    manifestPath: Path.Combine(dataDir, "browser", ElectronHostName + ".json"),
    relayExePath: relayExe,
    hostName: ElectronHostName);

// Which folder is Chrome actually loading? The user's explicitly-picked folder
// (BrowserExtensionPath) wins and survives restarts; otherwise the app's own
// deployed copy (the zero-config happy path). The id is derived from the folder.
string ActiveExtensionFolder() =>
    !string.IsNullOrWhiteSpace(settings.Current.BrowserExtensionPath) && Directory.Exists(settings.Current.BrowserExtensionPath)
        ? settings.Current.BrowserExtensionPath!
        : deployedExtension;

// Authorise one folder's id and drop the pairing token into it. Never mutates
// the folder's files (no footgun that could convert the WPF extension); a folder
// that is not an Electron-flavoured SRAN bundle simply will not connect, and the
// UI says so. Returns the derived id.
string ConfigureBrowserFor(string folder)
{
    var id = BrowserExtensionInstaller.UnpackedExtensionIdFor(folder);
    installer.Install(id);                                              // allowed_origins ← placeholder + id
    installer.ProvisionPairingToken(folder, bridge.GetOrCreatePairingToken());
    return id;
}

try
{
    var bundle = Path.Combine(AppContext.BaseDirectory, "browser-extension");
    if (Directory.Exists(bundle))
    {
        // Always refresh the deployed copy (bundle may change across app updates)
        // and rewrite it to the Electron flavour (collision #3): its background.js
        // connects to com.sran.browser.electron, and its manifest name carries an
        // "(Electron)" suffix so the owner can tell the two extensions apart.
        Directory.CreateDirectory(deployedExtension);
        foreach (var file in Directory.GetFiles(bundle))
        {
            if (Path.GetFileName(file).Equals(BrowserExtensionInstaller.PairingFileName, StringComparison.OrdinalIgnoreCase)) continue;
            File.Copy(file, Path.Combine(deployedExtension, Path.GetFileName(file)), overwrite: true);
        }
        ElectronBundle.Rewrite(deployedExtension, ElectronHostName, logger);

        // Write the relay's pipe config beside its exe (collision #1/lever D):
        // the WPF app's relay has no such file and stays on the default pipe.
        try { File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "browser-host.pipe"), electronPipeName); }
        catch (Exception ex) { logger.LogWarning(ex, "Could not write browser-host.pipe beside the relay; browser tools may not connect"); }

        var active = ActiveExtensionFolder();
        // FIX70: the id Chrome assigns is derived from the folder (UTF-16 hash,
        // fixed this round). A user-typed id that is well-formed and differs is
        // an explicit override (e.g. Chrome was given the path in another
        // spelling) and is KEPT — the previous code overwrote it at every start,
        // so a manual correction was silently undone by the next launch.
        var derivedId = BrowserExtensionInstaller.UnpackedExtensionIdFor(active);
        var storedId = settings.Current.BrowserExtensionId;
        // The pre-FIX70 derivation (UTF-8 hash) is what every install before this
        // one stored; it is the bug's output, never a user's choice — replace it.
        var legacyId = BrowserExtensionInstaller.LegacyUnpackedExtensionIdFor(active);
        var isOverride = HostQuerySurface.IsExtensionId(storedId) && storedId != derivedId && storedId != legacyId;
        var extensionId = isOverride ? storedId : derivedId;
        if (storedId == legacyId && legacyId != derivedId)
            logger.LogInformation("Browser: replacing the pre-FIX70 extension id {Legacy} with the one Chrome assigns, {Derived}", legacyId, derivedId);
        if (extensionId != derivedId)
            logger.LogWarning("Browser: keeping the user-set extension id {Stored} over the derived {Derived} for {Folder} (set the folder again to go back to the derived id)",
                storedId, derivedId, active);
        installer.Install(extensionId);                                       // allowed_origins ← placeholder + id
        installer.ProvisionPairingToken(active, bridge.GetOrCreatePairingToken());
        if (settings.Current.BrowserExtensionId != extensionId)
        {
            var s = settings.Current.Clone();
            s.BrowserExtensionId = extensionId;
            settings.Save(s);
        }
        logger.LogInformation("Browser: host={Host} pipe={Pipe} active-folder={Folder} (unpacked id {Id}); token provisioned (value never logged)",
            ElectronHostName, electronPipeName, active, extensionId);
    }
    else
    {
        logger.LogWarning("No browser-extension bundle beside the sidecar ({Bundle}); browser tools will report the extension as not connected", bundle);
    }
}
catch (Exception ex)
{
    logger.LogWarning(ex, "Browser bridge provisioning failed; browser tools will fail softly");
}
bridge.Start();

// The host-only browser reconfigure the escape-hatch picker forwards to
// (browser.applyExtensionFolder). The HOST supplies a path its NATIVE dialog
// produced; the page never names it. Validates like a folder grant, authorises
// the picked folder's id, drops the token, and persists it as the active folder.
(bool ok, object? data, string? error) ApplyExtensionFolder(string path)
{
    var rejection = FolderGrantValidator.Validate(path, out var canonical);
    if (rejection != FolderGrantRejection.Ok || canonical is null)
    {
        logger.LogWarning("browser.applyExtensionFolder REFUSED: {Rejection}", rejection);
        return (false, null, "refused");
    }
    try
    {
        var id = ConfigureBrowserFor(canonical);
        var s = settings.Current.Clone();
        s.BrowserExtensionPath = canonical;
        s.BrowserExtensionId = id;
        settings.Save(s);
        logger.LogInformation("browser.applyExtensionFolder: active folder set to {Folder} (id {Id})", canonical, id);
        return (true, new { folder = canonical, extensionId = id, connected = bridge.IsConnected }, null);
    }
    catch (Exception ex)
    {
        logger.LogWarning(ex, "browser.applyExtensionFolder failed");
        return (false, null, "internal-error");
    }
}

// FIX70: a well-formed id typed on the page is authorised at once (no restart).
bool ApplyExtensionId(string id)
{
    try
    {
        installer.Install(id);
        installer.ProvisionPairingToken(ActiveExtensionFolder(), bridge.GetOrCreatePairingToken());
        logger.LogInformation("Browser: extension id set by the user ({Id}) — manifest re-authorised", id);
        return true;
    }
    catch (Exception ex)
    {
        logger.LogWarning(ex, "Browser: could not re-authorise the user-set extension id");
        return false;
    }
}
// FIX70: mint a fresh pairing token (the old one is refused from now on), drop
// the live connection, and write the new token into the active folder's
// pairing.json so the extension self-heals on its next connect. The page learns
// only that it happened — the value never leaves the host/sidecar.
(bool ok, object? data, string? error) RotatePairingToken()
{
    try
    {
        var token = bridge.RotatePairingToken();
        var provisioned = installer.ProvisionPairingToken(ActiveExtensionFolder(), token);
        logger.LogInformation("Browser: pairing token rotated; pairing.json re-provisioned={Provisioned} (value never logged)", provisioned);
        return (true, new { rotated = true, provisioned, connected = bridge.IsConnected }, null);
    }
    catch (Exception ex)
    {
        logger.LogWarning(ex, "Browser: pairing token rotation failed");
        return (false, null, "internal-error");
    }
}
object BrowserStatus() => new
{
    connected = bridge.IsConnected,
    extensionFolder = ActiveExtensionFolder(),
    extensionId = settings.Current.BrowserExtensionId,
    hostName = ElectronHostName,
};


// ── MCP connectors (remote tools) ────────────────────────────────────────────
var connectors = new McpConnectorService(
    credentials, loggerFactory.CreateLogger<McpConnectorService>(),
    catalogUrl: Environment.GetEnvironmentVariable("SRAN_MCP_CATALOG_URL"),
    tokenSession: null, // oauth2 connectors need the portal JWT — not available here (E4 §1 #5)
    stateFilePath: Path.Combine(dataDir, "connectors.json"));

// Dev seam (S4): --seed-stdio-connector <id> <name> <command> [args...] installs a
// custom stdio connector into this data folder's connectors.json and exits.
if (args.Length >= 4 && args[0] == "--seed-stdio-connector")
{
    var connector = new McpConnector
    {
        Id = args[1], Name = args[2], Description = "E4 seeded stdio connector", Category = "developer",
        AuthType = "none", Transport = McpConnector.TransportStdio, Command = args[3], Args = args.Skip(4).ToList()
    };
    await connectors.InstallCustomAsync(connector);
    logger.LogInformation("Seeded stdio connector {Id} ({Command} {Args})", connector.Id, connector.Command, string.Join(' ', connector.Args ?? new()));
    return 0;
}
try
{
    await connectors.RefreshRemoteToolsAsync();
    logger.LogInformation("Remote MCP tools cached: {Count}", connectors.GetCachedRemoteToolDefinitions().Count);
}
catch (Exception ex)
{
    logger.LogWarning(ex, "Connector refresh failed; remote tools unavailable until the next refresh");
}

// ── the registry, in AddSranCore's order ─────────────────────────────────────
var screen = new ScreenContext();
var foreground = new ForegroundTargetTracker();
var registry = new McpToolRegistry();
registry.Register(new ReadFileTool());
registry.Register(new ReadDocumentTool(ocr: null, canRelayOcr: true)); // FIX3 §4: no JWT here — a scanned PDF's bytes (path+consent cleared) relay to the host, which holds the session, to OCR
registry.Register(new WriteFileTool());
registry.Register(new EditFileTool());
registry.Register(new CreateDirectoryTool());
registry.Register(new MoveFileTool());
registry.Register(new ListDirectoryTool());
registry.Register(new SearchFilesTool());
// [Clipboard slot: read_clipboard / write_clipboard → the Electron host]
registry.Register(new RunCommandTool(loggerFactory.CreateLogger<RunCommandTool>(), settings: null, session: session));
registry.Register(new UpdatePlanTool());
registry.Register(new RememberTool(store, session));
// [web_search → the Electron host (agent key)]
registry.Register(new WebFetchTool(httpFactory, loggerFactory.CreateLogger<WebFetchTool>()));
registry.Register(new UseSkillTool(skills));
// [ComputerUse slot: the Windows pack, constructed directly — the sidecar has no DI]
registry.Register(new ComputerScreenshotTool(screen, foreground));
registry.Register(new ComputerClickTool(screen, foreground));
registry.Register(new ComputerTypeTool());
registry.Register(new ComputerKeyTool());
registry.Register(new ComputerScrollTool(screen));
registry.Register(new ComputerDragTool(screen));
registry.Register(new ComputerZoomTool(screen));
registry.Register(new OpenApplicationTool());
// [UiAutomation slot: NOT registered — owner decision; Infrastructure.Windows.Desktop is not referenced]
registry.Register(new BrowserListTabsTool(bridge));
registry.Register(new BrowserTabsTool(bridge));
registry.Register(new BrowserNavigateTool(bridge));
registry.Register(new BrowserReadPageTool(bridge));
registry.Register(new BrowserFindTool(bridge));
registry.Register(new BrowserClickTool(bridge));
registry.Register(new BrowserTypeTool(bridge));
registry.Register(new BrowserScreenshotTool(bridge));
registry.Register(new OpenUrlTool(bridge, launcher));
// [run_python → the Electron host (portal JWT)]  [run_subagent → the Electron host]
var host = new McpToolHost(registry, connectors);
logger.LogInformation("Registered {Local} local tool(s) + {Remote} remote", registry.All.Count, connectors.GetCachedRemoteToolDefinitions().Count);

using var shutdown = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) => { e.Cancel = true; shutdown.Cancel(); };

// FIX6: resolve the origin a browser action targets, from the LIVE tab (tabs.list
// over the bridge). The host uses this for the per-site gate; the site is never a
// value the model supplied — tabId only picks WHICH real tab. Bridge down / no tab
// → (false), which the host turns into a legible "could not determine the site".
async Task<(bool ok, string? url, string? site)> ResolveBrowserSite(int? tabId)
{
    if (!bridge.IsConnected) return (false, null, null);
    try
    {
        var tabs = await bridge.RequestAsync("tabs.list", null, CancellationToken.None);
        var url = BrowserSite.ResolveTabUrl(tabs, tabId);
        var site = BrowserSite.SiteScope(url);
        return (site is not null, url, site);
    }
    catch
    {
        return (false, null, null);
    }
}

// FIX2: the host-only control surface (settings/skills/tasks/connectors/notes).
var hostQuery = new HostQuerySurface(settings, skills, taskStore, connectors, store, BrowserStatus, ApplyExtensionFolder, ResolveBrowserSite, loggerFactory.CreateLogger<HostQuerySurface>(),
    applyExtensionId: ApplyExtensionId, rotatePairingToken: RotatePairingToken);

var server = new StdioMcpServer(host, session, wireIn, wireOut, loggerFactory.CreateLogger<StdioMcpServer>(),
    () => new { tools = registry.All.Count, remoteTools = connectors.GetCachedRemoteToolDefinitions().Count, browserConnected = bridge.IsConnected, browserExtensionFolder = ActiveExtensionFolder(), browserHostName = ElectronHostName, dataDir },
    hostQuery);
try
{
    await server.RunAsync(shutdown.Token);
}
catch (OperationCanceledException) { }
await bridge.DisposeAsync();
await connectors.DisposeAsync();
logger.LogInformation("SranDesktop.ToolsSidecar exiting");
return 0;
