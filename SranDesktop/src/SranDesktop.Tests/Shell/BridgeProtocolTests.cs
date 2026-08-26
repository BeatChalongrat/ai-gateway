using System.Text.Json;
using FluentAssertions;
using SranDesktop.Shell.Bridge;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// Phase 3 §4.3 — the rejection paths, as unit tests against the pure parser
/// (the live counterparts are in SHELL_PHASE3_REPORT.md; the report states
/// per path which demonstration is live and which is this file).
/// </summary>
public sealed class BridgeProtocolTests
{
    private const string Nonce = "test-nonce-abcdefghijklmnopqrstuvwxyz012345";

    private static string Envelope(string type, string payloadJson = "{}", string? nonce = Nonce) =>
        nonce is null
            ? $$"""{"type":"{{type}}","payload":{{payloadJson}}}"""
            : $$"""{"nonce":"{{nonce}}","type":"{{type}}","payload":{{payloadJson}}}""";

    // ---------------- envelope: nonce (C-3) ----------------

    [Fact]
    public void A_Missing_Nonce_Is_Dropped()
    {
        BridgeProtocol.TryParseInbound(Envelope("ui.ready", nonce: null), Nonce, out var reason)
            .Should().BeNull();
        reason.Should().Be("nonce-missing");
    }

    [Fact]
    public void A_Wrong_Nonce_Is_Dropped()
    {
        BridgeProtocol.TryParseInbound(Envelope("ui.ready", nonce: "guessed"), Nonce, out var reason)
            .Should().BeNull();
        reason.Should().Be("nonce-mismatch");
    }

    // ---------------- envelope: closed type set (C-4) ----------------

    [Theory]
    [InlineData("turn.execute")]      // plausible fifth verb
    [InlineData("tool.invoke")]       // the verb the design forbids outright
    [InlineData("TURN.SUBMIT")]       // case variance is not tolerated
    [InlineData("")]
    public void An_Unknown_Type_Is_Dropped(string type)
    {
        BridgeProtocol.TryParseInbound(Envelope(type), Nonce, out var reason).Should().BeNull();
        reason.Should().StartWith("unknown-type");
    }

    [Theory]
    [InlineData("not json at all")]
    [InlineData("[1,2,3]")]
    [InlineData("\"just a string\"")]
    public void A_Structurally_Wrong_Envelope_Is_Dropped(string json)
    {
        BridgeProtocol.TryParseInbound(json, Nonce, out _).Should().BeNull();
    }

    [Fact]
    public void The_Four_Declared_Types_Parse()
    {
        foreach (var wire in new[] { "turn.submit", "turn.cancel", "consent.respond", "ui.ready" })
            BridgeProtocol.TryParseInbound(Envelope(wire), Nonce, out _).Should().NotBeNull(wire);
    }

    // ---------------- turn.submit: handles are opaque, never paths ----------------

    [Theory]
    [InlineData(@"C:\Users\x\secrets.txt")]
    [InlineData("/etc/passwd")]
    [InlineData("../../appsettings.json")]
    [InlineData("file:///C:/x")]
    [InlineData("a/b")]
    [InlineData(@"a\b")]
    [InlineData("a:b")]
    [InlineData("a b")]
    [InlineData("")]
    public void A_Path_Shaped_Attachment_Handle_Rejects_The_Whole_Submit(string handle)
    {
        var payload = JsonSerializer.SerializeToElement(new
        {
            conversationId = "conv-1",
            text = "hello",
            attachmentHandles = new[] { handle },
        });
        BridgeProtocol.TryParseTurnSubmit(payload, out var reason).Should().BeNull();
        reason.Should().Be("attachmentHandle-path-shaped",
            "a handle that can name a path must reject the message, not be resolved");
    }

    [Fact]
    public void An_Opaque_Handle_Parses()
    {
        var payload = JsonSerializer.SerializeToElement(new
        {
            conversationId = "conv-1",
            text = "hello",
            attachmentHandles = new[] { "att_0123456789abcdef" },
            modelId = "auto",
        });
        var parsed = BridgeProtocol.TryParseTurnSubmit(payload, out _);
        parsed.Should().NotBeNull();
        parsed!.AttachmentHandles.Should().ContainSingle().Which.Should().Be("att_0123456789abcdef");
        parsed.ModelId.Should().Be("auto");
    }

    [Fact]
    public void A_Submit_Without_ConversationId_Or_Text_Is_Rejected()
    {
        var noConv = JsonSerializer.SerializeToElement(new { text = "hi" });
        BridgeProtocol.TryParseTurnSubmit(noConv, out _).Should().BeNull();
        var noText = JsonSerializer.SerializeToElement(new { conversationId = "c" });
        BridgeProtocol.TryParseTurnSubmit(noText, out _).Should().BeNull();
    }

    // ---------------- ui.ready (handshake + reload recovery) ----------------

    [Fact]
    public void UiReady_With_No_Payload_Is_A_Plain_Handshake()
    {
        var parsed = BridgeProtocol.TryParseUiReady(default, out _);
        parsed.Should().Be(new UiReadyPayload(null, 0));
    }

    [Fact]
    public void UiReady_Carries_Conversation_And_Last_Seq_For_Resume()
    {
        var payload = JsonSerializer.SerializeToElement(new { conversationId = "conv-9", lastSeq = 12 });
        var parsed = BridgeProtocol.TryParseUiReady(payload, out _);
        parsed.Should().Be(new UiReadyPayload("conv-9", 12));
    }

    [Theory]
    [InlineData("{\"lastSeq\": -1}")]
    [InlineData("{\"lastSeq\": \"12\"}")]
    [InlineData("{\"conversationId\": 5}")]
    public void UiReady_With_A_Malformed_Field_Is_Rejected(string payloadJson)
    {
        using var doc = JsonDocument.Parse(payloadJson);
        BridgeProtocol.TryParseUiReady(doc.RootElement, out var reason).Should().BeNull();
        reason.Should().NotBeEmpty();
    }

    // ---------------- consent.respond ----------------

    [Theory]
    [InlineData("allow", "once", true)]
    [InlineData("allow", "session", true)]
    [InlineData("deny", "once", true)]
    [InlineData("approve", "once", false)]   // not a declared decision
    [InlineData("allow", "forever", false)]  // not a declared scope
    [InlineData("", "once", false)]
    public void Consent_Decisions_And_Scopes_Are_A_Closed_Set(string decision, string scope, bool ok)
    {
        var payload = JsonSerializer.SerializeToElement(new { promptId = "p1", decision, scope });
        var parsed = BridgeProtocol.TryParseConsentRespond(payload, out _);
        (parsed is not null).Should().Be(ok);
    }
}
