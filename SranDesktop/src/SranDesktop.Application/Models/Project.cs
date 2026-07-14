using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>
/// A persistent workspace grouping conversations with custom instructions and
/// files (R9 Feature B). chat-edge has no projects API, so projects live
/// locally in %APPDATA%\SRAN Desktop\projects.json (files copied under
/// projects\{id}\files\).
/// </summary>
public sealed class Project
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = Guid.NewGuid().ToString("N");

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    /// <summary>Custom system-prompt fragment applied to every chat in the project.</summary>
    [JsonPropertyName("instructions")]
    public string? Instructions { get; set; }

    /// <summary>Absolute paths of files copied into the project folder.</summary>
    [JsonPropertyName("files")]
    public List<string> Files { get; set; } = new();

    /// <summary>Conversations created inside this project.</summary>
    [JsonPropertyName("conversation_ids")]
    public List<string> ConversationIds { get; set; } = new();

    [JsonPropertyName("created_at")]
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("updated_at")]
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}
