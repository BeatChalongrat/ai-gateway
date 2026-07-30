using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>User-editable settings persisted to %APPDATA%\SRAN Desktop\settings.json.</summary>
public sealed class UserSettings
{
    [JsonPropertyName("chat_edge_base_url")]
    public string ChatEdgeBaseUrl { get; set; } = "http://localhost:4000";

    [JsonPropertyName("gateway_base_url")]
    public string GatewayBaseUrl { get; set; } = "http://localhost:8080";

    [JsonPropertyName("default_model")]
    public string DefaultModel { get; set; } = "gpt-4o-mini";

    /// <summary>"Dark" or "Light".</summary>
    [JsonPropertyName("theme")]
    public string Theme { get; set; } = "Dark";

    [JsonPropertyName("minimize_to_tray")]
    public bool MinimizeToTray { get; set; } = true;

    [JsonPropertyName("start_with_windows")]
    public bool StartWithWindows { get; set; }

    /// <summary>MRU model ids for the picker's "Recently used" section (max 5).</summary>
    [JsonPropertyName("recent_models")]
    public List<string> RecentModels { get; set; } = new();

    /// <summary>UI language: "en" or "th". Empty = auto-detect from the OS culture.</summary>
    [JsonPropertyName("locale")]
    public string Locale { get; set; } = "";

    /// <summary>AI-generated writing style prompt fragment; empty = none (R9 L).</summary>
    [JsonPropertyName("writing_style_profile")]
    public string WritingStyleProfile { get; set; } = "";

    /// <summary>Dev/load-unpacked Chrome extension id added to the browser
    /// bridge's native-messaging allowed_origins (browser tools). Empty =
    /// store id only.</summary>
    [JsonPropertyName("browser_extension_id")]
    public string BrowserExtensionId { get; set; } = "";

    /// <summary>Voice settings (R9 J): TTS voice name, rate (-10..10), auto-read.</summary>
    [JsonPropertyName("voice_name")]
    public string VoiceName { get; set; } = "";

    [JsonPropertyName("voice_rate")]
    public int VoiceRate { get; set; }

    [JsonPropertyName("voice_auto_read")]
    public bool VoiceAutoRead { get; set; }

    /// <summary>Speech backend (R10 Fix 2): "auto" | "windows" | "azure".
    /// Auto prefers Azure whenever a key is stored.</summary>
    [JsonPropertyName("speech_backend")]
    public string SpeechBackend { get; set; } = "auto";

    /// <summary>STT language for dictation, e.g. "th-TH". Empty = backend default.</summary>
    [JsonPropertyName("speech_language")]
    public string SpeechLanguage { get; set; } = "";

    /// <summary>Azure Speech region (key lives in DPAPI, never here).</summary>
    [JsonPropertyName("azure_speech_region")]
    public string AzureSpeechRegion { get; set; } = "southeastasia";

    /// <summary>Opt-in crash telemetry (R11 A3). Default OFF — user's choice.</summary>
    [JsonPropertyName("crash_reporting_enabled")]
    public bool CrashReportingEnabled { get; set; }

    /// <summary>One-way backup of agent-mode chats to the user's account
    /// (Phase 3 D). Default ON; when off the sync service does nothing.</summary>
    [JsonPropertyName("sync_agent_conversations")]
    public bool SyncAgentConversations { get; set; } = true;

    /// <summary>Generate Excel/Word plan files on this device when the plan is
    /// fully supported locally (Phase 12 E). Default OFF for parity: the
    /// server path stays authoritative until local generation is proven.
    /// Local files keep content on-device but skip the server-side validator
    /// and version lineage; unsupported plans (pptx, pdf, charts, edits of
    /// uploaded files, headers/footers) always use the server.</summary>
    [JsonPropertyName("prefer_local_office_generation")]
    public bool PreferLocalOfficeGeneration { get; set; }

    /// <summary>Extra folders the agent's file tools may reach, beyond the
    /// built-in user-profile + temp roots (Phase 6 Task 1). Each is validated
    /// (existing directory, not a drive/system root) before being applied.</summary>
    [JsonPropertyName("granted_folders")]
    public List<string> GrantedFolders { get; set; } = new();

    /// <summary>run_command timeout in seconds (Phase A PART 3). Defaults high
    /// enough for a real .NET/npm build or test run; the old 30s hard cap killed
    /// them mid-run. Clamped to a sane range by the tool.</summary>
    [JsonPropertyName("shell_timeout_seconds")]
    public int ShellTimeoutSeconds { get; set; } = 300;

    /// <summary>The agent's working folder, chosen from the composer (Cowork-style).
    /// Acts as an extra granted root and is surfaced to the model as the default
    /// place to do file work. Empty = none selected.</summary>
    [JsonPropertyName("working_folder")]
    public string WorkingFolder { get; set; } = "";

    /// <summary>Global approval mode for agent tool calls (Cowork-style):
    /// "manual" (pause for every action), "auto" (run freely, pause only for
    /// unsafe actions), or "skip" (never pause). Default "auto".</summary>
    [JsonPropertyName("approval_mode")]
    public string ApprovalMode { get; set; } = "auto";

    /// <summary>
    /// Which lane <c>run_command</c> executes in.
    ///
    /// <para>"sandbox" (default) runs every command inside the capability-free
    /// AppContainer: no network, an allowlisted environment whose PATH is only
    /// System32, and a staged private copy of the workspace. Host tooling
    /// (docker, git, node, python) is unreachable there by construction — the
    /// CLI is a client of a host service and the sandbox exists to sever
    /// exactly that.</para>
    ///
    /// <para>"host" runs the command on the real machine with the real
    /// environment, so host tooling works. This is a deliberate, user-chosen
    /// widening of the security boundary, never a fallback: a sandbox failure
    /// must not reach this lane (see
    /// <c>RuntimeBypassInvariantTests.Host_Lane_Is_Never_Reached_By_Fallback</c>).
    /// The working directory is still confined to an authorized root, and the
    /// command still passes the blocklist and the normal consent gate.</para>
    ///
    /// <para><b>Risk, stated plainly:</b> with <see cref="ApprovalMode"/> set to
    /// "skip", host-lane commands run with no prompt. The exposure is not a
    /// careless click — it is prompt injection: content the model just read
    /// from a web page or a file can dictate the command. Every host-lane
    /// dispatch is written to the security audit log for this reason.</para>
    /// </summary>
    [JsonPropertyName("shell_execution_mode")]
    public string ShellExecutionMode { get; set; } = "sandbox";

    public UserSettings Clone()
    {
        var copy = (UserSettings)MemberwiseClone();
        copy.RecentModels = new List<string>(RecentModels);
        copy.GrantedFolders = new List<string>(GrantedFolders);
        return copy;
    }
}
