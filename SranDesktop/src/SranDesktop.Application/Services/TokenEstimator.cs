using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>
/// Deliberately rough token estimator for context budgeting — no tokenizer
/// dependency. Text counts as ~4 characters per token, every image block
/// costs a flat <see cref="ImageTokenEstimate"/>, and each message carries a
/// small fixed overhead. The estimate can be off in either direction; callers
/// must budget with a safety margin (see <see cref="ContextBudgeter"/>) and
/// treat provider context errors as the ground truth.
/// </summary>
public static class TokenEstimator
{
    private const int CharsPerToken = 4;
    private const int PerMessageOverhead = 4;
    private const int ImageTokenEstimate = 1100;

    public static int EstimateMessages(IEnumerable<GatewayMessage> messages) =>
        messages.Sum(EstimateMessage);

    public static int EstimateMessage(GatewayMessage message)
    {
        var tokens = PerMessageOverhead;

        switch (message.Content)
        {
            case string text:
                tokens += TokensForChars(text.Length);
                break;
            case IEnumerable<object> blocks:
                foreach (var block in blocks)
                    tokens += EstimateBlock(block);
                break;
        }

        if (message.ToolCalls is { Count: > 0 })
        {
            foreach (var call in message.ToolCalls)
                tokens += TokensForChars(
                    call.Function.Name.Length + call.Function.Arguments.Length);
        }

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
            return TokensForChars(block.ToString()?.Length ?? 0);
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
                    return TokensForChars(text.GetString()!.Length);
            }
        }

        return TokensForChars(json.GetRawText().Length);
    }

    private static int TokensForChars(int chars) =>
        (chars + CharsPerToken - 1) / CharsPerToken;
}
