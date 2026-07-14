using System.Text.Json;

namespace SranDesktop.Application.Models;

/// <summary>
/// Factory + detector for the vision message the agent loop appends after a
/// screenshot tool result. Construction and detection live together so
/// <see cref="Services.ScreenshotPruner"/> cannot drift from the message
/// shape <c>ChatOrchestrator</c> builds.
/// </summary>
public static class ScreenshotMessage
{
    public const string TextMarker = "[Screenshot from";

    /// <summary>Tool-role content stays plain text for provider compatibility;
    /// the image follows as a user message with OpenAI multimodal blocks.</summary>
    public static GatewayMessage Create(string toolName, string dataUri) => new()
    {
        Role = "user",
        Content = new object[]
        {
            new { type = "text", text = $"{TextMarker} {toolName} — analyze it to decide your next action]" },
            new { type = "image_url", image_url = new { url = dataUri } }
        }
    };

    /// <summary>Structural detection: user role, multimodal blocks, a text block
    /// starting with the marker, and an image block. Pruned messages are plain
    /// strings and no longer match.</summary>
    public static bool IsScreenshotMessage(GatewayMessage message)
    {
        if (message.Role != "user" || message.Content is not IEnumerable<object> blocks)
            return false;

        var hasMarkerText = false;
        var hasImage = false;
        foreach (var block in blocks)
        {
            JsonElement json;
            try
            {
                json = JsonSerializer.SerializeToElement(block);
            }
            catch (NotSupportedException)
            {
                continue;
            }

            if (json.ValueKind != JsonValueKind.Object
                || !json.TryGetProperty("type", out var type)
                || type.ValueKind != JsonValueKind.String)
                continue;

            switch (type.GetString())
            {
                case "text" when json.TryGetProperty("text", out var text)
                                 && text.ValueKind == JsonValueKind.String
                                 && text.GetString()!.StartsWith(TextMarker, StringComparison.Ordinal):
                    hasMarkerText = true;
                    break;
                case "image_url":
                    hasImage = true;
                    break;
            }
        }

        return hasMarkerText && hasImage;
    }
}
