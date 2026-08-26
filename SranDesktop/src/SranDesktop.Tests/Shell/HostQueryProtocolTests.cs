using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Shell.Bridge;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// Phase 5 §2.3 — the refusal rules of the new control vocabulary, as unit tests
/// against the pure parser + the validator behind the one genuinely dangerous
/// op. Where a rule is demonstrated live instead, the report says so and points
/// here for the rest. These deliberately use inputs a HOSTILE page would send
/// (a real path in a grant, a type-confused setting value, a forbidden settings
/// key) rather than merely malformed JSON.
/// </summary>
public sealed class HostQueryProtocolTests
{
    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    private static JsonElement Payload(string op, object? args = null, string requestId = "req-1") =>
        JsonSerializer.SerializeToElement(args is null
            ? (object)new { requestId, op }
            : new { requestId, op, args });

    // ── the op set is closed ──────────────────────────────────────────────

    [Theory]
    [InlineData("skills.wipe")]        // plausible sibling of a real op
    [InlineData("tool.invoke")]        // the verb the whole design forbids
    [InlineData("host.exec")]          // generic dispatch, which must not exist
    [InlineData("settings.delete")]    // no such op
    [InlineData("SKILLS.LIST")]        // case variance is not tolerated
    [InlineData("")]
    public void An_Unknown_Op_Is_Dropped(string op)
    {
        HostQueryProtocol.TryParseHostQuery(Payload(op), out var reason).Should().BeNull();
        reason.Should().StartWith("unknown-op");
    }

    [Fact]
    public void Every_Declared_Op_Parses()
    {
        foreach (var wire in new[]
                 {
                     "skills.list", "skills.setEnabled", "skills.installPicker", "skills.delete",
                     "tasks.list", "tasks.create", "tasks.update", "tasks.setEnabled", "tasks.delete", "tasks.runNow",
                     "connectors.listCatalog", "connectors.listInstalled", "connectors.install", "connectors.uninstall",
                     "connectors.oauthConnect", "connectors.oauthDisconnect", "connectors.addCustomDialog",
                     "settings.get", "settings.set",
                     "folders.list", "folders.pickAndGrant", "folders.revoke",
                     "globalNotes.list", "globalNotes.delete",
                     "diag.reportRendererError",
                 })
            HostQueryProtocol.TryParseHostQuery(Payload(wire), out _).Should().NotBeNull(wire);
    }

    [Fact]
    public void A_Missing_Or_Oversized_RequestId_Is_Dropped()
    {
        HostQueryProtocol.TryParseHostQuery(
            JsonSerializer.SerializeToElement(new { op = "skills.list" }), out var r1).Should().BeNull();
        r1.Should().Be("requestId-invalid");

        var huge = new string('x', 200);
        HostQueryProtocol.TryParseHostQuery(Payload("skills.list", requestId: huge), out var r2).Should().BeNull();
        r2.Should().Be("requestId-invalid");
    }

    [Theory]
    [InlineData("\"a string\"")]
    [InlineData("[1,2,3]")]
    [InlineData("42")]
    public void A_Non_Object_Payload_Is_Dropped(string json)
    {
        using var doc = JsonDocument.Parse(json);
        HostQueryProtocol.TryParseHostQuery(doc.RootElement, out _).Should().BeNull();
    }

    // ── settings.set — the key allow-list (§2.3) ──────────────────────────

    [Theory]
    [InlineData("granted_folders")]  // native-picker only; never a settings.set key
    [InlineData("approval_mode")]    // a separate, native-gated decision
    [InlineData("azure_speech_region")]
    [InlineData("browser_pairing_token")]  // a secret; never settable
    [InlineData("shell_timeout_seconds")]
    public void A_Settings_Key_Outside_The_Allow_List_Is_Refused(string key)
    {
        HostQueryProtocol.ParseSettingKey(key).Should().BeNull(
            "a key not in SettingKey must be dropped, not written");
    }

    [Fact]
    public void A_Type_Confused_Settings_Value_Is_Refused()
    {
        // A hostile page sends the right key with the wrong-typed value.
        HostQueryProtocol.TryReadSettingValue(SettingKey.MinimizeToTray, Args(new { value = "yes" }), out _)
            .Should().BeFalse("a boolean setting must not accept a string");
        HostQueryProtocol.TryReadSettingValue(SettingKey.Theme, Args(new { value = "Neon" }), out _)
            .Should().BeFalse("theme is a closed set of three");
        HostQueryProtocol.TryReadSettingValue(SettingKey.ChatEdgeBaseUrl, Args(new { value = "not a url" }), out _)
            .Should().BeFalse("an endpoint must be an absolute http(s) URI");
        HostQueryProtocol.TryReadSettingValue(SettingKey.GatewayBaseUrl, Args(new { value = "javascript:alert(1)" }), out _)
            .Should().BeFalse("a non-http scheme is refused");
    }

    [Fact]
    public void A_Well_Formed_Settings_Value_Is_Accepted_And_Normalized()
    {
        HostQueryProtocol.TryReadSettingValue(SettingKey.Theme, Args(new { value = "Light" }), out var theme)
            .Should().BeTrue();
        theme.Should().Be("Light");

        HostQueryProtocol.TryReadSettingValue(SettingKey.MinimizeToTray, Args(new { value = false }), out var tray)
            .Should().BeTrue();
        tray.Should().Be(false);

        HostQueryProtocol.TryReadSettingValue(SettingKey.GatewayBaseUrl, Args(new { value = "https://sran.ai/" }), out var url)
            .Should().BeTrue();
        url.Should().Be("https://sran.ai", "a trailing slash is trimmed to match how the WPF Save normalizes it");
    }

    // ── folders — the page cannot name a grant path (§2.3) ────────────────

    [Fact]
    public void FoldersPickAndGrant_Has_No_Path_Argument_Even_When_A_Hostile_Page_Adds_One()
    {
        // The refusal is STRUCTURAL: the op parses, but the parser exposes only
        // (op, args) — there is no path field to honour, and the dispatcher opens
        // the native picker regardless of args. A page that stuffs a path into
        // args gains nothing; this pins that the parse does not surface it as a
        // grantable value.
        var parsed = HostQueryProtocol.TryParseHostQuery(
            Payload("folders.pickAndGrant", new { path = @"C:\Windows\System32" }), out _);
        parsed.Should().NotBeNull();
        parsed!.Op.Should().Be(HostQueryOp.FoldersPickAndGrant);
        // There is no "grant this path" parser; the only path that can ever be
        // granted flows from the OS picker through FolderGrantValidator below.
    }

    [Theory]
    [InlineData(@"C:\")]                      // drive root — exposes the whole disk
    [InlineData(@"C:\Windows")]               // system tree
    [InlineData(@"C:\Windows\System32")]
    [InlineData("relative\\path")]            // not fully qualified
    [InlineData("")]
    public void The_Grant_Validator_Refuses_The_Paths_A_Hostile_Native_Pick_Might_Yield(string path)
    {
        // Even the native picker's result is validated (defence in depth): a
        // user who somehow points it at a drive/system root is still refused.
        FolderGrantValidator.Validate(path, out var canonical)
            .Should().NotBe(FolderGrantRejection.Ok);
        canonical.Should().BeNull();
    }
}
