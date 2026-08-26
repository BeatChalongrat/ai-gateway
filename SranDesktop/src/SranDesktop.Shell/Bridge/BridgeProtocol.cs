using System.Text.Json;

namespace SranDesktop.Shell.Bridge;

/// <summary>
/// The bridge vocabulary (DESKTOP_SHELL_PLAN.md §5) — four inbound types, four
/// outbound. This enum is CLOSED (C-4): parsing maps unknown strings to null
/// and the caller drops + logs; there is no default case that does something.
/// A fifth member is a design review, not a patch — pinned by
/// ShellPinningTests.The_Inbound_Vocabulary_Has_Exactly_Four_Types.
/// </summary>
public enum InboundMessageType
{
    TurnSubmit,
    TurnCancel,
    ConsentRespond,
    UiReady,
}

public enum OutboundMessageType
{
    TurnEvent,
    ConsentRequest,
    SessionChanged,
    HostError,
}

/// <summary>
/// One parsed inbound message. `Nonce` and `Type` are validated by
/// <see cref="BridgeProtocol.TryParseInbound"/>; `Payload` is the raw JSON
/// element for the per-type payload parsers below.
/// </summary>
public sealed record InboundMessage(InboundMessageType Type, JsonElement Payload);

public sealed record TurnSubmitPayload(
    string ConversationId,
    string Text,
    IReadOnlyList<string> AttachmentHandles,
    string? ModelId);

public sealed record TurnCancelPayload(string ConversationId);

public sealed record ConsentRespondPayload(string PromptId, string Decision, string Scope);

/// <summary>
/// ui.ready doubles as the handshake AND the reload-recovery probe (plan
/// addendum): on boot the page sends it with no conversation; after a reload
/// mid-turn it sends the conversation it is showing plus the last
/// <c>turn.event</c> seq it rendered, and the host replays from seq+1.
/// </summary>
public sealed record UiReadyPayload(string? ConversationId, long LastSeq);

/// <summary>
/// Wire parsing, pure (no WebView2 types) so every rejection path is
/// unit-testable. Every Try* returns null on ANY structural deviation —
/// wrong types, missing fields, path-shaped "handles" — never a best-effort
/// value.
/// </summary>
public static class BridgeProtocol
{
    public static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    /// <summary>Map the wire `type` string. Closed: anything else is null.</summary>
    public static InboundMessageType? ParseInboundType(string? type) => type switch
    {
        "turn.submit" => InboundMessageType.TurnSubmit,
        "turn.cancel" => InboundMessageType.TurnCancel,
        "consent.respond" => InboundMessageType.ConsentRespond,
        "ui.ready" => InboundMessageType.UiReady,
        _ => null,
    };

    public static string WireName(OutboundMessageType type) => type switch
    {
        OutboundMessageType.TurnEvent => "turn.event",
        OutboundMessageType.ConsentRequest => "consent.request",
        OutboundMessageType.SessionChanged => "session.changed",
        OutboundMessageType.HostError => "host.error",
        _ => throw new ArgumentOutOfRangeException(nameof(type)),
    };

    /// <summary>
    /// Parse + authenticate one inbound envelope:
    /// <c>{ "nonce": "...", "type": "...", "payload": { ... } }</c>.
    /// Returns null (with a machine-readable reason for the log) unless the
    /// JSON is an object, the nonce equals <paramref name="expectedNonce"/>
    /// (C-3), and the type is one of the four (C-4).
    /// </summary>
    /// <summary>
    /// Validate + authenticate the envelope WITHOUT mapping the type to any
    /// vocabulary: <c>{ "nonce": "...", "type": "...", "payload": { ... } }</c>.
    /// Returns the raw <paramref name="type"/> string and cloned
    /// <paramref name="payload"/> once the JSON is an object and the nonce equals
    /// <paramref name="expectedNonce"/> (C-3). This is the single shared gate for
    /// BOTH the turn vocabulary and the Phase 5 control vocabulary — the branch
    /// on which vocabulary a message belongs to happens ONE level up, in
    /// BridgeChannel, after this has run. Both branches are closed; an unknown
    /// type is dropped by whichever branch owns it.
    /// </summary>
    public static bool TryReadEnvelope(
        string json, string expectedNonce, out string type, out JsonElement payload, out string reason)
    {
        type = "";
        payload = default;
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(json);
        }
        catch (JsonException)
        {
            reason = "not-json";
            return false;
        }
        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                reason = "not-an-object";
                return false;
            }
            if (!root.TryGetProperty("nonce", out var nonceEl) || nonceEl.ValueKind != JsonValueKind.String)
            {
                reason = "nonce-missing";
                return false;
            }
            // Constant-time comparison is not required here (the nonce is a
            // capability check against page-internal confusion, not a network
            // secret), but exact equality is.
            if (!string.Equals(nonceEl.GetString(), expectedNonce, StringComparison.Ordinal))
            {
                reason = "nonce-mismatch";
                return false;
            }
            if (!root.TryGetProperty("type", out var typeEl) || typeEl.ValueKind != JsonValueKind.String)
            {
                reason = "type-missing";
                return false;
            }
            type = typeEl.GetString()!;
            payload = root.TryGetProperty("payload", out var payloadEl) ? payloadEl.Clone() : default;
            reason = "";
            return true;
        }
    }

    public static InboundMessage? TryParseInbound(string json, string expectedNonce, out string reason)
    {
        if (!TryReadEnvelope(json, expectedNonce, out var typeString, out var payload, out reason))
            return null;
        var type = ParseInboundType(typeString);
        if (type is null)
        {
            reason = $"unknown-type:{typeString}";
            return null;
        }
        reason = "";
        return new InboundMessage(type.Value, payload);
    }

    /// <summary>
    /// turn.submit payload. Rejects — whole message, not per-field salvage —
    /// when conversationId/text are absent or when any attachment "handle"
    /// looks like a filesystem path or URI rather than an opaque id. A page
    /// that can name a path is a page that can read the disk (plan §5).
    /// </summary>
    public static TurnSubmitPayload? TryParseTurnSubmit(JsonElement payload, out string reason)
    {
        if (payload.ValueKind != JsonValueKind.Object)
        {
            reason = "payload-not-object";
            return null;
        }
        if (!TryGetString(payload, "conversationId", out var conversationId) || conversationId.Length == 0)
        {
            reason = "conversationId-missing";
            return null;
        }
        if (!TryGetString(payload, "text", out var text))
        {
            reason = "text-missing";
            return null;
        }
        string? modelId = null;
        if (payload.TryGetProperty("modelId", out var modelEl))
        {
            if (modelEl.ValueKind != JsonValueKind.String && modelEl.ValueKind != JsonValueKind.Null)
            {
                reason = "modelId-not-string";
                return null;
            }
            modelId = modelEl.ValueKind == JsonValueKind.String ? modelEl.GetString() : null;
        }
        var handles = new List<string>();
        if (payload.TryGetProperty("attachmentHandles", out var handlesEl))
        {
            if (handlesEl.ValueKind != JsonValueKind.Array)
            {
                reason = "attachmentHandles-not-array";
                return null;
            }
            foreach (var el in handlesEl.EnumerateArray())
            {
                if (el.ValueKind != JsonValueKind.String)
                {
                    reason = "attachmentHandle-not-string";
                    return null;
                }
                var handle = el.GetString()!;
                if (!IsOpaqueHandle(handle))
                {
                    reason = "attachmentHandle-path-shaped";
                    return null;
                }
                handles.Add(handle);
            }
        }
        reason = "";
        return new TurnSubmitPayload(conversationId, text, handles, modelId);
    }

    public static TurnCancelPayload? TryParseTurnCancel(JsonElement payload, out string reason)
    {
        if (payload.ValueKind != JsonValueKind.Object
            || !TryGetString(payload, "conversationId", out var conversationId)
            || conversationId.Length == 0)
        {
            reason = "conversationId-missing";
            return null;
        }
        reason = "";
        return new TurnCancelPayload(conversationId);
    }

    public static ConsentRespondPayload? TryParseConsentRespond(JsonElement payload, out string reason)
    {
        if (payload.ValueKind != JsonValueKind.Object
            || !TryGetString(payload, "promptId", out var promptId) || promptId.Length == 0)
        {
            reason = "promptId-missing";
            return null;
        }
        if (!TryGetString(payload, "decision", out var decision)
            || decision is not ("allow" or "deny"))
        {
            reason = "decision-invalid";
            return null;
        }
        if (!TryGetString(payload, "scope", out var scope)
            || scope is not ("once" or "session"))
        {
            reason = "scope-invalid";
            return null;
        }
        reason = "";
        return new ConsentRespondPayload(promptId, decision, scope);
    }

    /// <summary>
    /// ui.ready: the nonce echo is already proven by the envelope (C-3). The
    /// payload optionally carries the conversation the page is showing and the
    /// last rendered seq, for reload recovery. Both absent = plain handshake.
    /// A present-but-wrong-typed field rejects the message (never a best-effort
    /// parse); absent fields default to (null, 0).
    /// </summary>
    public static UiReadyPayload? TryParseUiReady(JsonElement payload, out string reason)
    {
        reason = "";
        if (payload.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
            return new UiReadyPayload(null, 0);
        if (payload.ValueKind != JsonValueKind.Object)
        {
            reason = "payload-not-object";
            return null;
        }
        string? conversationId = null;
        if (payload.TryGetProperty("conversationId", out var convEl))
        {
            if (convEl.ValueKind == JsonValueKind.String)
                conversationId = convEl.GetString();
            else if (convEl.ValueKind != JsonValueKind.Null)
            {
                reason = "conversationId-not-string";
                return null;
            }
        }
        long lastSeq = 0;
        if (payload.TryGetProperty("lastSeq", out var seqEl))
        {
            if (seqEl.ValueKind != JsonValueKind.Number || !seqEl.TryGetInt64(out lastSeq) || lastSeq < 0)
            {
                reason = "lastSeq-invalid";
                return null;
            }
        }
        return new UiReadyPayload(conversationId, lastSeq);
    }

    /// <summary>An opaque attachment handle: short, single-token, and free of
    /// every character a path or URI needs. Deny-by-shape, not allow-by-luck.</summary>
    public static bool IsOpaqueHandle(string value)
    {
        if (value.Length is 0 or > 128)
            return false;
        foreach (var c in value)
        {
            var ok = c is (>= 'a' and <= 'z') or (>= 'A' and <= 'Z') or (>= '0' and <= '9') or '-' or '_';
            if (!ok)
                return false;
        }
        return true;
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
