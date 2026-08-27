using System.Text.Json.Nodes;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native.Browser;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.ToolsSidecar;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// FIX1: the separation levers (so both apps run at once) and the §4.7 invariant
/// that the control plane is NEVER reachable as a tool.
/// </summary>
public class Fix1BrowserSeparationTests
{
    // ── §1.1 collision #1: distinct pipe, WPF pipe byte-identical ─────────────

    [Fact]
    public void PipeName_Default_Is_Unchanged_And_The_Electron_Tag_Is_Distinct()
    {
        var wpf = BrowserWire.PipeName();
        // Null and empty tag MUST hash the identity alone — the pre-FIX1 name, so
        // the WPF app's pipe does not move.
        BrowserWire.PipeName(null).Should().Be(wpf);
        BrowserWire.PipeName("").Should().Be(wpf);
        // The Electron tag MUST produce a different endpoint, or both apps collide
        // on the single-instance pipe and the second one is dead.
        BrowserWire.PipeName("electron").Should().NotBe(wpf);
        // Still the same shape (short, char-safe).
        BrowserWire.PipeName("electron").Should().StartWith("sran-browser-");
    }

    // ── §1.2: the folder is load-bearing; the id derives from it deterministically ─

    [Fact]
    public void UnpackedExtensionId_Is_Deterministic_Per_Folder()
    {
        var a = BrowserExtensionInstaller.UnpackedExtensionIdFor(@"C:\one\browser-extension");
        var b = BrowserExtensionInstaller.UnpackedExtensionIdFor(@"C:\one\browser-extension");
        var c = BrowserExtensionInstaller.UnpackedExtensionIdFor(@"C:\two\browser-extension");
        a.Should().Be(b);                 // same folder → same id
        a.Should().NotBe(c);              // different folder → different id
        a.Should().MatchRegex("^[a-p]{32}$"); // Chrome's 0-9a-f → a-p mapping, 32 chars
    }

    // ── §4.7: the control plane answers on the host-only surface, and is NEVER a tool ─

    [Fact]
    public async Task ControlPlane_Ops_Answer_On_The_Surface_But_Are_Never_Tools()
    {
        // The full page↔host control vocabulary + the sidecar-internal browser op.
        // If any of these ever becomes a registered TOOL name, this test fails.
        string[] controlPlaneOps =
        {
            "settings.get", "settings.set",
            "skills.list", "skills.setEnabled", "skills.installPicker", "skills.delete",
            "tasks.list", "tasks.create", "tasks.update", "tasks.setEnabled", "tasks.delete", "tasks.runNow",
            "connectors.listCatalog", "connectors.listInstalled", "connectors.install", "connectors.uninstall",
            "connectors.oauthConnect", "connectors.oauthDisconnect", "connectors.addCustomDialog",
            "folders.list", "folders.pickAndGrant", "folders.revoke",
            "globalNotes.list", "globalNotes.delete", "diag.reportRendererError", "session.logout",
            "browser.pickExtensionFolder", "browser.applyExtensionFolder",
        };

        // A registry that includes the FIX1 browser tools plus a representative
        // spread of the sidecar's tools. tools/list is exactly these names.
        var bridge = new BrowserBridgeService(new NullCredentialStore(), NullLogger<BrowserBridgeService>.Instance);
        var registry = new McpToolRegistry();
        registry.Register(new ReadFileTool());
        registry.Register(new WriteFileTool());
        registry.Register(new ListDirectoryTool());
        registry.Register(new BrowserListTabsTool(bridge));
        registry.Register(new BrowserNavigateTool(bridge));
        registry.Register(new BrowserClickTool(bridge));
        registry.Register(new BrowserScreenshotTool(bridge));
        // Internal host↔sidecar ops (never in the page's vocabulary, never tools):
        string[] internalOps =
        {
            "skills._install", "tasks._due", "tasks._recordRun",
            "connectors._installApiKey", "connectors._testCustom", "connectors._installCustom",
            "skills._forPrompt", // FIX28 §3
        };
        var toolNames = registry.All.Select(t => t.Name).ToHashSet();

        // The rule: tools ∩ (control-plane ∪ internal) = ∅. No tool may share a name.
        toolNames.Intersect(controlPlaneOps.Concat(internalOps)).Should().BeEmpty(
            "the model's only vocabulary is tools/list + tools/call; a control-plane op must never appear there");

        // The control plane is reachable — off the tool channel — via the host-only
        // surface, now for the whole ported set (FIX2). A real skills/tasks/connectors/
        // notes surface, backed by temp stores.
        var tmp = Path.Combine(Path.GetTempPath(), "fix2-" + Guid.NewGuid().ToString("N"));
        var settings = new JsonSettingsService(new UserSettings(), Path.Combine(tmp, "settings.json"), NullLogger<JsonSettingsService>.Instance);
        var skills = new SranDesktop.Infrastructure.Services.SkillManager(Path.Combine(tmp, "skills"), NullLogger<SranDesktop.Infrastructure.Services.SkillManager>.Instance);
        var taskStore = new SranDesktop.Infrastructure.Storage.SqliteScheduledTaskStore(Path.Combine(tmp, "tasks.db"));
        var connectors = new SranDesktop.Infrastructure.Services.McpConnectorService(new NullCredentialStore(), NullLogger<SranDesktop.Infrastructure.Services.McpConnectorService>.Instance, null, null, Path.Combine(tmp, "connectors.json"));
        var convStore = new SranDesktop.Infrastructure.Storage.SqliteConversationStore(Path.Combine(tmp, "data.db"));
        await convStore.InitializeAsync();
        var surface = new HostQuerySurface(settings, skills, taskStore, connectors, convStore,
            () => new { connected = false }, _ => (false, null, "not-tested"),
            _ => Task.FromResult<(bool, string?, string?)>((false, null, null)), NullLogger<HostQuerySurface>.Instance);

        // Ported ops answer on the surface (NOT 'unsupported' — that was FIX1's state):
        (await surface.DispatchAsync("settings.get", null)).ok.Should().BeTrue();
        (await surface.DispatchAsync("skills.list", null)).ok.Should().BeTrue();
        (await surface.DispatchAsync("tasks.list", null)).ok.Should().BeTrue();
        (await surface.DispatchAsync("connectors.listInstalled", null)).ok.Should().BeTrue();
        (await surface.DispatchAsync("globalNotes.list", null)).ok.Should().BeTrue();
        // FIX6: the per-action site resolver is a host-only INTERNAL op (never a
        // tool). With no bridge it answers a legible error — not 'unsupported' —
        // proving it is a known op, and its site comes from the tab, not the caller.
        var siteFor = await surface.DispatchAsync("browser._siteForAction", null);
        siteFor.ok.Should().BeFalse();
        siteFor.error.Should().Contain("bridge");
        // A genuinely unknown op still answers 'unsupported' (a coarse code, never a tool):
        (await surface.DispatchAsync("bogus.op", null)).error.Should().Be("unsupported");
        // oauth is closed with a legible reason, not silence:
        var oauth = await surface.DispatchAsync("connectors.install", new JsonObject { ["connectorId"] = "nope" });
        oauth.error.Should().Be("not-found"); // (no such catalog id here; the oauth path is exercised in the host test)

        // settings.set honours the CLOSED allow-list (mirrors HostQueryProtocol).
        (await surface.DispatchAsync("settings.set", new JsonObject { ["key"] = "theme", ["value"] = "Light" })).ok.Should().BeTrue();
        settings.Current.Theme.Should().Be("Light");
        var refused = await surface.DispatchAsync("settings.set", new JsonObject { ["key"] = "grantedFolders", ["value"] = "x" });
        refused.ok.Should().BeFalse();
        refused.error.Should().Be("refused"); // grants go through folders.* + native picker, never settings.set

        // FIX25 ยง1b/ยง1c: the approval mode is page-settable through the SAME closed
        // op (no new op), with a closed vocabulary โ€” a fourth mode is 'invalid', not
        // laundered to auto by the parser โ€” and it round-trips in the snapshot the
        // host reads per tool call.
        (await surface.DispatchAsync("settings.set", new JsonObject { ["key"] = "approvalMode", ["value"] = "skip" })).ok.Should().BeTrue();
        settings.Current.ApprovalMode.Should().Be("skip");
        var badMode = await surface.DispatchAsync("settings.set", new JsonObject { ["key"] = "approvalMode", ["value"] = "yolo" });
        badMode.ok.Should().BeFalse();
        badMode.error.Should().Be("invalid");
        settings.Current.ApprovalMode.Should().Be("skip"); // the bad write changed nothing
        var snap = System.Text.Json.JsonSerializer.SerializeToNode((await surface.DispatchAsync("settings.get", null)).data)!.AsObject();
        snap["approvalMode"]!.GetValue<string>().Should().Be("skip");
    }

    // What this test CANNOT see: it builds a representative registry, not the
    // sidecar's live process, so it cannot prove a future tool won't be given a
    // dotted control-plane name at runtime — but the sidecar builds tools ONLY via
    // this same McpToolRegistry.Register path, and host/query is a separate JSON-RPC
    // method that ListTools() never enumerates, so the namespaces cannot cross.

    private sealed class NullCredentialStore : ICredentialStore
    {
        private readonly Dictionary<string, string> _d = new();
        public void Save(string key, string value) => _d[key] = value;
        public string? Load(string key) => _d.GetValueOrDefault(key);
        public void Delete(string key) => _d.Remove(key);
    }
}
