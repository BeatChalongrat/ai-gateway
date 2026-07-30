namespace SranDesktop.Domain.Enums;

/// <summary>Which lane <c>run_command</c> executes in. The vocabulary matches
/// the two runners: the AppContainer sandbox and the real host.</summary>
public enum ShellLane
{
    /// <summary>Capability-free AppContainer: no network, PATH is only System32,
    /// and the command sees a staged copy of one authorized workspace.</summary>
    Sandbox,

    /// <summary>The real machine with the real environment, so host tooling
    /// works. Opt-in only, and never reached by recovering from a sandbox
    /// failure.</summary>
    Host
}

/// <summary>
/// Maps the persisted <c>shell_execution_mode</c> string to the enum.
///
/// <para>This exists so "what counts as the host lane" is written down once.
/// The lane decision is a security boundary; two independently written string
/// comparisons — one in the tool, one in the settings UI — is how a UI that
/// says "sandbox" ends up in front of a runner that disagrees.</para>
///
/// <para>Anything unrecognized parses to <see cref="ShellLane.Sandbox"/>. A
/// typo, a truncated write, or a settings file from a future version must fail
/// toward isolation, never away from it.</para>
/// </summary>
public static class ShellLaneParser
{
    public static ShellLane Parse(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "host" => ShellLane.Host,
        _ => ShellLane.Sandbox
    };

    public static string ToSettingsValue(ShellLane lane) => lane switch
    {
        ShellLane.Host => "host",
        _ => "sandbox"
    };
}
