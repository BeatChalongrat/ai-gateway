namespace SranDesktop.Domain.Enums;

/// <summary>Global approval mode for agent tool calls (Cowork-style dropdown):
/// how much the agent pauses for user consent before executing a tool.</summary>
public enum ApprovalMode
{
    /// <summary>Pause so the user can approve each action.</summary>
    Manual,

    /// <summary>Run on its own; pause to ask only when an action looks unsafe.</summary>
    Auto,

    /// <summary>Never pause, even for unsafe actions.</summary>
    Skip
}

/// <summary>Maps the persisted settings string ("manual" | "auto" | "skip")
/// to the enum. Unknown/empty values fall back to Auto (the default).</summary>
public static class ApprovalModeParser
{
    public static ApprovalMode Parse(string? value) => value?.ToLowerInvariant() switch
    {
        "manual" => ApprovalMode.Manual,
        "skip" => ApprovalMode.Skip,
        _ => ApprovalMode.Auto
    };

    public static string ToSettingsValue(ApprovalMode mode) => mode switch
    {
        ApprovalMode.Manual => "manual",
        ApprovalMode.Skip => "skip",
        _ => "auto"
    };
}
