using System.Text;
using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>
/// Approximate token estimator for context budgeting — no tokenizer
/// dependency. This is a PORT of the server's estimator
/// (services/chat-edge-api/internal/service/token_estimator.go): text is
/// counted per CODE POINT, latin at ~4 chars/token and dense scripts
/// (Thai, CJK, Kana, Hangul, fullwidth) at ~1.5 chars/token, because those
/// scripts have no spaces and tokenize far denser in BPE schemes. Counting
/// Thai at the latin ratio under-estimated it by roughly 2.7x, which let a
/// long Thai conversation look like it fit when it did not — a silent
/// failure, since the provider simply rejected the request or answered
/// worse.
///
/// The two implementations are pinned to one shared corpus
/// (tests/fixtures/token_estimator_corpus.json) so neither side can drift
/// alone. Change the model on both sides or not at all.
///
/// Still an estimate, not a tokenizer: it errs toward counting MORE so the
/// budget trims sooner, callers keep a safety margin (see
/// <see cref="ContextBudgeter"/>), and a provider context error remains the
/// ground truth.
/// </summary>
public static class TokenEstimator
{
    private const int LatinCharsPerToken = 4;
    private const int PerMessageOverhead = 4;
    private const int PerToolCallOverhead = 8;

    /// <summary>
    /// Flat cost of one image block. DELIBERATELY not the server's 500: the
    /// desktop agent loop feeds back full-screen captures at high detail
    /// (~1100 tokens under OpenAI's tiling for a 1080p frame), where the
    /// server's figure describes typically smaller user attachments. Keeping
    /// the larger number here counts conservatively for the client that
    /// sends the most images — lowering it would re-introduce exactly the
    /// silent under-count this estimator exists to prevent. Text estimation
    /// is what the shared corpus pins; this constant is intentionally local.
    /// </summary>
    private const int ImageTokenEstimate = 1100;

    public static int EstimateMessages(IEnumerable<GatewayMessage> messages) =>
        messages.Sum(EstimateMessage);

    /// <summary>
    /// Token estimate for a bare string. 0 for empty input; never below 1
    /// for anything else. Mirrors EstimateTokens in token_estimator.go.
    /// </summary>
    public static int EstimateText(string? text)
    {
        if (string.IsNullOrEmpty(text))
            return 0;

        var latin = 0;
        var dense = 0;
        // Enumerate RUNES, not chars: an astral emoji is two UTF-16 units
        // but one code point, and the server walks code points. Counting
        // UTF-16 units here would silently disagree on any surrogate pair.
        foreach (var rune in text.EnumerateRunes())
        {
            if (IsDenseScript(rune.Value))
                dense++;
            else
                latin++;
        }

        // latin: floor(n/4). dense: ceil(2n/3) ~ one token per 1.5 runes.
        var tokens = latin / LatinCharsPerToken + (dense * 2 + 2) / 3;
        return tokens < 1 ? 1 : tokens;
    }

    public static int EstimateMessage(GatewayMessage message)
    {
        var tokens = PerMessageOverhead;

        switch (message.Content)
        {
            case string text:
                tokens += EstimateText(text);
                break;
            case IEnumerable<object> blocks:
                foreach (var block in blocks)
                    tokens += EstimateBlock(block);
                break;
        }

        if (message.ToolCalls is { Count: > 0 })
        {
            foreach (var call in message.ToolCalls)
                tokens += EstimateText(call.Function.Name)
                          + EstimateText(call.Function.Arguments)
                          + PerToolCallOverhead;
        }

        // The tool-result envelope carries the function name on the wire, so
        // it costs tokens too (the server counts it; we used to not).
        if (!string.IsNullOrEmpty(message.Name))
            tokens += EstimateText(message.Name);

        return tokens;
    }

    /// <summary>OpenAI-style content block: {type:"text",...} or {type:"image_url",...}.</summary>
    private static int EstimateBlock(object block)
    {
        JsonElement json;
        try
        {
            json = JsonSerializer.SerializeToElement(block);
        }
        catch (NotSupportedException)
        {
            return EstimateText(block.ToString());
        }

        if (json.ValueKind == JsonValueKind.Object
            && json.TryGetProperty("type", out var type)
            && type.ValueKind == JsonValueKind.String)
        {
            switch (type.GetString())
            {
                case "image_url":
                    return ImageTokenEstimate;
                case "text" when json.TryGetProperty("text", out var text)
                                 && text.ValueKind == JsonValueKind.String:
                    return EstimateText(text.GetString());
            }
        }

        return EstimateText(json.GetRawText());
    }

    /// <summary>
    /// Scripts that tokenize at ~1.5 chars/token instead of the latin ~4.
    /// Ranges are kept identical to isCJKOrThai in token_estimator.go.
    /// </summary>
    private static bool IsDenseScript(int codePoint) => codePoint switch
    {
        >= 0x0E00 and <= 0x0E7F => true, // Thai
        >= 0x4E00 and <= 0x9FFF => true, // CJK Unified Ideographs
        >= 0x3040 and <= 0x30FF => true, // Hiragana + Katakana
        >= 0xAC00 and <= 0xD7AF => true, // Hangul Syllables
        >= 0xFF00 and <= 0xFFEF => true, // Halfwidth / Fullwidth forms
        _ => false
    };
}
