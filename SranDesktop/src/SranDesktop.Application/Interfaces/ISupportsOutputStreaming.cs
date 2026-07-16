namespace SranDesktop.Application.Interfaces;

/// <summary>One incremental chunk of a tool's live output (Phase B PART 1).</summary>
public sealed record ToolOutputChunk(string ToolName, string Text);

/// <summary>
/// Opt-in surface (Phase B PART 1) for a tool that streams output WHILE it runs
/// — run_command emits stdout/stderr lines as a build/test progresses instead of
/// only returning a blob at the end. ONLY RunCommandTool implements it; the
/// ChatViewModel consumer checks <c>is ISupportsOutputStreaming</c> and
/// subscribes, appending chunks to the pending tool card (a mini terminal). The
/// model's final <c>tool</c> result is unchanged in shape — streaming is
/// additive UI, and the full captured output still flows through the normal
/// (masked, capped) tool-result pipeline.
/// </summary>
public interface ISupportsOutputStreaming
{
    /// <summary>Raised (on the executing/reader thread) for each output line as
    /// it arrives. The consumer marshals to the UI thread.</summary>
    event Action<ToolOutputChunk>? OutputStreamed;
}
