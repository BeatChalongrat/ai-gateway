using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>manifest.json of a skill folder (R9 Feature H).</summary>
public sealed class SkillManifest
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("displayName")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("description")]
    public string Description { get; set; } = "";

    [JsonPropertyName("version")]
    public string Version { get; set; } = "1.0.0";

    /// <summary>Case-insensitive substrings that activate this skill.</summary>
    [JsonPropertyName("triggers")]
    public List<string> Triggers { get; set; } = new();

    [JsonPropertyName("icon")]
    public string? Icon { get; set; }

    [JsonPropertyName("author")]
    public string? Author { get; set; }
}

/// <summary>A loaded skill: manifest + SKILL.md instructions.</summary>
public sealed class Skill
{
    public required SkillManifest Manifest { get; init; }

    /// <summary>SKILL.md content, prepended to the system prompt when triggered.</summary>
    public required string Instructions { get; init; }

    /// <summary>Folder on disk this skill was loaded from.</summary>
    public required string Path { get; init; }

    public bool IsBuiltin { get; init; }
    public bool IsEnabled { get; set; } = true;
}
