using System.Text.Json;
using FluentAssertions;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// Shell Phase 1 pinning tests (DESKTOP_SHELL_PLAN.md §4, "pinned by test,
/// following the repo's own pattern" — CanvasHostObjectTests).
///
/// These read SOURCE TEXT because the properties they pin are ABSENCES: there
/// is no runtime API that reports "no host object was registered" or "no frame
/// handler was attached".
///
/// LIMITS — stated rather than implied, as CanvasSandboxTests does:
///   * A source-text assertion passes if the file is renamed or moved out of
///     the scanned tree (mitigated here by scanning every *.cs under the
///     project, not one named file — but a second project would not be seen).
///   * It passes if the registration is built by reflection
///     (<c>typeof(CoreWebView2).GetMethod("AddHostObjectToScript")</c>) or by
///     string concatenation of the member name.
///   * It passes if the forbidden call lives in a referenced library rather
///     than in this project's own source.
///   * It cannot see runtime configuration: the allow-list test reads the
///     checked-in appsettings.json, not the file a user has beside the exe.
/// Comment lines are stripped before matching so that a comment explaining WHY
/// an API is absent may name it.
/// </summary>
public sealed class ShellPinningTests
{
    private static DirectoryInfo SourceRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "SranDesktop.Shell")))
            dir = dir.Parent;
        dir.Should().NotBeNull("the Shell project must be locatable from the test output");
        return dir!;
    }

    private static string ShellProjectDir() => Path.Combine(SourceRoot().FullName, "SranDesktop.Shell");

    /// <summary>Every C# file in the shell project, comments stripped (line and
    /// XML-doc comments; block comments are not used in this project).</summary>
    private static IEnumerable<(string File, string Code)> ShellSources()
    {
        var root = ShellProjectDir();
        var files = Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories)
            .Where(f => !f.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}")
                     && !f.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}"))
            .ToList();
        files.Should().NotBeEmpty("the shell project must contain source files");
        foreach (var file in files)
        {
            var code = string.Join('\n', File.ReadAllLines(file)
                .Where(line => !line.TrimStart().StartsWith("//", StringComparison.Ordinal)));
            yield return (file, code);
        }
    }

    [Fact]
    public void The_Shell_Never_Registers_A_Host_Object()
    {
        // C-1. AddHostObjectToScript would hand a .NET object graph to page JS —
        // and, via spike finding 6, to any sandboxed artifact iframe in it.
        // (Limits: see class summary — reflection or string-built member names
        // are invisible to this assertion.)
        foreach (var (file, code) in ShellSources())
            code.Should().NotContain("AddHostObjectToScript", $"{file} must not register a host object");
    }

    [Fact]
    public void The_Shell_Never_Registers_A_Frame_Message_Handler()
    {
        // C-1, second half: CoreWebView2Frame.WebMessageReceived is the delivery
        // path an about:srcdoc iframe used to reach the host in the spike
        // (finding 6). Phase 3 adds ONE top-level CoreWebView2.WebMessageReceived
        // (the bridge) — allowed — but a FRAME handler, or FrameCreated (the only
        // way to obtain a CoreWebView2Frame to hook), must never appear. This is
        // the narrowed assertion Phase 1's comment promised; it did not get
        // deleted. (Limits: see class summary — a reflected/string-built member
        // name is invisible, as is a handler introduced by a referenced DLL.)
        foreach (var (file, code) in ShellSources())
        {
            code.Should().NotContain("FrameCreated",
                $"{file} must not enumerate frames (the only way to reach CoreWebView2Frame.WebMessageReceived)");
            code.Should().NotContain("CoreWebView2Frame",
                $"{file} must not name the frame type whose WebMessageReceived a sandboxed iframe reaches");
        }
    }

    [Fact]
    public void Only_The_Bridge_Channel_Handles_Web_Messages()
    {
        // C-1/C-2: the top-level WebMessageReceived registration must live in
        // exactly one file — BridgeChannel — so the origin/nonce/enum checks
        // cannot be bypassed by a second, laxer handler elsewhere. (Limit: a
        // handler attached from a referenced DLL, or via reflection, is not
        // visible to a source-text scan.)
        foreach (var (file, code) in ShellSources())
        {
            if (Path.GetFileName(file) == "BridgeChannel.cs")
                continue;
            code.Should().NotContain("WebMessageReceived",
                $"{file} must not register a web-message handler — only BridgeChannel may");
            code.Should().NotContain("AddHostObjectToScript",
                $"{file} must not register a host object");
        }
    }

    [Fact]
    public void The_Inbound_Vocabulary_Has_Exactly_Four_Types()
    {
        // Plan §5: "Four in, four out. A fifth is a design review, not a patch."
        // A fifth inbound member would widen the untrusted surface silently;
        // this fails if InboundMessageType gains or loses a member. (Limit: it
        // asserts the ENUM's shape — a handler that dispatches on a raw wire
        // string bypassing the enum would not be caught here; the closed switch
        // in BridgeChannel + BridgeProtocol.ParseInboundType are what force all
        // dispatch through this enum, and those are exercised by the unit tests.)
        var names = Enum.GetNames(typeof(SranDesktop.Shell.Bridge.InboundMessageType));
        names.Should().BeEquivalentTo(new[] { "TurnSubmit", "TurnCancel", "ConsentRespond", "UiReady" },
            "the inbound vocabulary is closed at four (turn.submit, turn.cancel, consent.respond, ui.ready)");
    }

    [Fact]
    public void The_Control_Vocabulary_Is_Exactly_One_In_One_Out()
    {
        // Phase 5 §2.2: the ONE new pair, host.query / host.result, is its own
        // closed set — SEPARATE from the four turn types so the turn vocabulary
        // (and the test above) stays untouched. A second control-inbound member
        // would be another privileged verb minted without review; this fails if
        // that happens.
        Enum.GetNames(typeof(SranDesktop.Shell.Bridge.ControlInboundType))
            .Should().BeEquivalentTo(new[] { "HostQuery" },
                "the control vocabulary is one-in (host.query) …");
        Enum.GetNames(typeof(SranDesktop.Shell.Bridge.ControlOutboundType))
            .Should().BeEquivalentTo(new[] { "HostResult" },
                "… and one-out (host.result)");
    }

    [Fact]
    public void The_Host_Query_Op_Set_Is_Closed_And_Named()
    {
        // Phase 5 §2.2 / criterion 3. The security property is that every
        // privileged operation is named INDIVIDUALLY — not that there are N of
        // them. So this asserts the exact NAMES, not a count: a rename, an
        // addition, or a deletion all fail here, where a count test would let a
        // rename slip past. Adding an op without adding its name below is the
        // "design review, not a patch" made mechanical.
        Enum.GetNames(typeof(SranDesktop.Shell.Bridge.HostQueryOp))
            .Should().BeEquivalentTo(new[]
            {
                "SkillsList", "SkillsSetEnabled", "SkillsInstallPicker", "SkillsDelete",
                "TasksList", "TasksCreate", "TasksUpdate", "TasksSetEnabled", "TasksDelete", "TasksRunNow",
                "ConnectorsListCatalog", "ConnectorsListInstalled", "ConnectorsInstall", "ConnectorsUninstall",
                "ConnectorsOauthConnect", "ConnectorsOauthDisconnect", "ConnectorsAddCustomDialog",
                "SettingsGet", "SettingsSet",
                "FoldersList", "FoldersPickAndGrant", "FoldersRevoke",
                "GlobalNotesList", "GlobalNotesDelete",
                "DiagReportRendererError",
            });
    }

    [Fact]
    public void The_Settings_Key_Allow_List_Is_Closed_And_Named_And_Excludes_The_Dangerous_Ones()
    {
        // Phase 5 §2.3. settings.set may write only these keys. The exact names
        // are pinned for the same reason the op set is. And two keys are asserted
        // ABSENT by name because their absence is load-bearing, not incidental:
        //   granted_folders — only folders.* + the native picker may touch it;
        //   approval_mode   — changing the agent's own consent gating is a
        //                     separate, native-gated decision, never a settings key.
        Enum.GetNames(typeof(SranDesktop.Shell.Bridge.SettingKey))
            .Should().BeEquivalentTo(new[]
            {
                "Theme", "MinimizeToTray", "StartWithWindows", "CrashReportingEnabled",
                "SyncAgentConversations", "PreferLocalOfficeGeneration", "DefaultModel",
                "ChatEdgeBaseUrl", "GatewayBaseUrl", "BrowserExtensionId", "WritingStyleProfile", "Locale",
            });
        // The wire mapper must refuse the two dangerous keys outright.
        SranDesktop.Shell.Bridge.HostQueryProtocol.ParseSettingKey("granted_folders").Should().BeNull();
        SranDesktop.Shell.Bridge.HostQueryProtocol.ParseSettingKey("approval_mode").Should().BeNull();
    }

    [Fact]
    public void The_Navigation_Allow_List_Is_Non_Empty_And_Has_No_Wildcard()
    {
        // C-5. An empty list would refuse everything (safe but useless); a
        // wildcard would refuse nothing. NavigationPolicy also throws on both at
        // startup — this pins the CHECKED-IN configuration, which a runtime
        // check cannot. (Limit: the file a user places beside the exe is not
        // what is read here.)
        var path = Path.Combine(ShellProjectDir(), "appsettings.json");
        File.Exists(path).Should().BeTrue($"expected {path}");
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var origins = doc.RootElement.GetProperty("Shell").GetProperty("AllowedOrigins")
            .EnumerateArray().Select(e => e.GetString() ?? "").ToList();

        origins.Should().NotBeEmpty();
        foreach (var origin in origins)
        {
            origin.Should().NotContain("*");
            origin.Should().NotBeNullOrWhiteSpace();
            Uri.TryCreate(origin, UriKind.Absolute, out var uri).Should().BeTrue($"'{origin}' must be an absolute origin");
            uri!.PathAndQuery.Should().Be("/", $"'{origin}' must be an origin, not a URL");
        }
    }

    [Fact]
    public void The_Shell_Project_Does_Not_Reference_Presentation()
    {
        // Abandonment rule 2 (plan §8): the shell references Infrastructure,
        // Application and Domain only. A Presentation reference is how the
        // "half-moved" wreckage case starts. (Limit: a transitive reference
        // introduced through another project, or a <Reference> to the built
        // DLL, is not a ProjectReference and would not be seen here.)
        var csproj = Path.Combine(ShellProjectDir(), "SranDesktop.Shell.csproj");
        File.Exists(csproj).Should().BeTrue($"expected {csproj}");
        // XML comments stripped for the same reason .cs comments are: the
        // comment that explains the rule names the forbidden project.
        var text = System.Text.RegularExpressions.Regex.Replace(
            File.ReadAllText(csproj), "<!--.*?-->", "", System.Text.RegularExpressions.RegexOptions.Singleline);

        text.Should().NotContain("SranDesktop.Presentation");
        text.Should().NotMatchRegex("""<Reference\s""", "a raw DLL reference would be the same rule broken differently");
        text.Should().Contain("SranDesktop.Infrastructure.csproj");
        text.Should().Contain("SranDesktop.Application.csproj");
        text.Should().Contain("SranDesktop.Domain.csproj");
    }

    [Fact]
    public void Only_The_Bridge_Channel_Posts_Web_Messages()
    {
        // Phase 2 forbade PostWebMessage anywhere (no protocol yet). Phase 3
        // introduces it, but confined to BridgeChannel — the same one file that
        // owns the inbound handler — so host→page traffic has a single typed
        // choke point. (Limits: see class summary.)
        foreach (var (file, code) in ShellSources())
        {
            if (Path.GetFileName(file) == "BridgeChannel.cs")
                continue;
            code.Should().NotContain("PostWebMessage",
                $"{file} must not post web messages — only BridgeChannel may");
        }
    }

    [Fact]
    public void The_Portal_Origin_Is_Configured_And_Not_Allow_Listed()
    {
        // Phase 2 §2.2: the portal redirect is the re-auth signal. An
        // allow-listed portal origin would let the WebView load the login web
        // app, handing session acquisition back to the page. NavigationPolicy
        // also throws on this at startup — this pins the CHECKED-IN config.
        var path = Path.Combine(ShellProjectDir(), "appsettings.json");
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var shell = doc.RootElement.GetProperty("Shell");
        var portal = shell.GetProperty("PortalOrigin").GetString();
        portal.Should().NotBeNullOrWhiteSpace();
        var origins = shell.GetProperty("AllowedOrigins")
            .EnumerateArray().Select(e => e.GetString() ?? "").ToList();
        origins.Should().NotContain(o => string.Equals(o.TrimEnd('/'), portal!.TrimEnd('/'), StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public void The_WebView2_Package_Is_Pinned_Exactly()
    {
        // Audit U-9: Presentation floats "1.*"; the shell pins the version the
        // spike verified. An NuGet exact-version range is "[x.y.z]".
        var csproj = Path.Combine(ShellProjectDir(), "SranDesktop.Shell.csproj");
        var text = File.ReadAllText(csproj);
        text.Should().MatchRegex("""Include="Microsoft\.Web\.WebView2"\s+Version="\[\d+\.\d+\.\d+\.\d+\]" """.Trim());
    }
}
