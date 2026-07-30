using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Code interpreter (Phase 13 Part B): a THIN CLIENT that forwards
/// model-authored Python to chat-edge's <c>POST /api/v1/code/run</c>
/// endpoint, which runs it in the server-side hardened sandbox (no network,
/// non-root, cap-drop ALL, seccomp default-deny, read-only rootfs, stripped
/// env, resource-capped). The desktop process never executes the code — it
/// only relays the request and renders the result (stdout + any chart image).
///
/// Consent posture: DefaultPermission = Ask (like run_command / remote tools),
/// deny-without-handler. Rationale vs run_command: this does NOT touch the
/// real machine (execution is remote + sandboxed with no network and no
/// filesystem access beyond the disposable box), so it is materially LESS
/// dangerous than a local shell. But it still (a) executes arbitrary
/// model-authored code and (b) can read the user's server-side conversation
/// files, so it is NOT auto-allowed — it asks, and the user may grant
/// allow-once / allow-always per session through the standard consent flow.
/// </summary>
public sealed class RunPythonTool : IMcpTool, ISensitiveDataTool
{
    private readonly IChatEdgeApiClient _client;

    public RunPythonTool(IChatEdgeApiClient client) => _client = client;

    public string Name => "run_python";

    public string Description =>
        "Execute Python in a secure, isolated server sandbox (no network) and return its stdout/stderr. " +
        "Use it for real computation, data analysis, math, or to generate a chart — instead of guessing. " +
        "numpy, pandas, matplotlib and openpyxl are available (use matplotlib.use('Agg')). print() what " +
        "you want to see. To attach a chart image to your reply, save it to /job/out/ " +
        "(e.g. plt.savefig('/job/out/chart.png')). Read the output, then refine and run again or explain " +
        "the result. Do NOT invent output you did not see.";

    public ToolCategory Category => ToolCategory.Network; // execution is remote; no local side effect

    // Ask — see the class remarks for the justification vs run_command.
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "RemoteSandbox.Execute",
        "CodeAndFiles.SendToSandbox",
        "model_authored_code",
        "unknown",
        "configured code sandbox service",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "code": { "type": "string", "description": "The Python source to run. Self-contained; use print() to surface results." },
                "files": {
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "Optional file_ids of conversation files to mount read-only at /job/in/<name>."
                }
            },
            "required": ["code"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var code = ToolHelpers.GetString(arguments, "code");
        if (string.IsNullOrWhiteSpace(code))
            return ToolResult.Fail("'code' is required.");

        var files = ReadFileIds(arguments);

        CodeExecutionResult result;
        try
        {
            result = await _client.RunPythonAsync(code, files, ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"Code interpreter call failed: {ex.Message}");
        }

        return Shape(result);
    }

    private static List<string>? ReadFileIds(JsonElement args)
    {
        if (args.ValueKind != JsonValueKind.Object
            || !args.TryGetProperty("files", out var files)
            || files.ValueKind != JsonValueKind.Array)
            return null;

        var ids = new List<string>();
        foreach (var item in files.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String && item.GetString() is { Length: > 0 } id)
                ids.Add(id);
        }
        return ids.Count > 0 ? ids : null;
    }

    private static ToolResult Shape(CodeExecutionResult r)
    {
        var sb = new StringBuilder();
        if (r.TimedOut)
            sb.AppendLine("[The code timed out and was stopped.]");
        sb.AppendLine($"Exit code: {r.Exit}");

        var stdout = (r.Stdout ?? string.Empty).TrimEnd('\n');
        if (stdout.Length > 0)
        {
            sb.AppendLine();
            sb.AppendLine("stdout:");
            sb.AppendLine(stdout);
        }
        var stderr = (r.Stderr ?? string.Empty).TrimEnd('\n');
        if (stderr.Length > 0 && (r.Exit != 0 || stdout.Length == 0))
        {
            sb.AppendLine();
            sb.AppendLine("stderr:");
            sb.AppendLine(stderr);
        }
        if (r.Truncated)
            sb.AppendLine("[output truncated]");
        if (stdout.Length == 0 && stderr.Length == 0)
            sb.AppendLine("(no output was printed)");

        var images = r.Images ?? new List<CodeExecutionImage>();
        var otherFiles = r.Files ?? new List<CodeExecutionFile>();
        if (images.Count > 0 || otherFiles.Count > 0)
        {
            sb.AppendLine();
            if (images.Count > 0)
                sb.AppendLine("Generated image(s): " + string.Join(", ", images.Select(i => i.Filename)));
            if (otherFiles.Count > 0)
                sb.AppendLine("Generated file(s): " + string.Join(", ", otherFiles.Select(f => f.Filename)));
        }

        var text = UntrustedObservationBoundary.ProtectIfUntrusted(
            "run_python", sb.ToString().TrimEnd());

        // ToolResult carries at most one inline image (for the model's vision).
        // Surface the first chart; any extras are named in the text above.
        if (images.Count > 0 && !string.IsNullOrEmpty(images[0].DataUri))
            return ToolResult.OkWithImage(text, images[0].DataUri);

        return ToolResult.Ok(text);
    }
}
