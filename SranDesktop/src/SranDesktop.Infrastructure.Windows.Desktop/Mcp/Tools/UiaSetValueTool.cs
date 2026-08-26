using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Uia;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// uia_set_value (UIA layer) — fills an input element from the last
/// read_screen_elements snapshot. ValuePattern.SetValue is atomic and
/// layout-independent; the fallback focuses the element and retypes
/// (select-all first so it replaces rather than appends). Shares
/// computer_type's hard credential block — same egress channel.
/// </summary>
public sealed class UiaSetValueTool : IMcpTool
{
    private readonly UiaSnapshotStore _snapshots;

    public UiaSetValueTool(UiaSnapshotStore snapshots) => _snapshots = snapshots;

    public string Name => "uia_set_value";
    public string Description =>
        "Set the text value of an input element by its id from read_screen_elements. " +
        "More reliable than clicking + typing. Element ids are ONLY valid from the LATEST " +
        "read_screen_elements result — never guess ids; call read_screen_elements first.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "element_id": { "type": "integer", "description": "Element id from the last read_screen_elements call" },
                "text": { "type": "string", "description": "The value to set (replaces the current content)" }
            },
            "required": ["element_id", "text"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("element_id", out var idEl) || !idEl.TryGetInt32(out var elementId))
            return ToolResult.Fail("element_id integer is required.");
        var text = ToolHelpers.GetString(arguments, "text");
        if (text is null)
            return ToolResult.Fail("text is required.");

        if (ComputerTypeTool.LooksLikeCredential(text, out var kind))
            return ToolResult.Fail(
                $"Refused: the text looks like a {kind}. Typing credentials, keys, or card " +
                "numbers is prohibited — ask the user to enter it themselves.");

        if (!_snapshots.TryGet(elementId, out var stored, out var staleError))
            return ToolResult.Fail(staleError!);

        try
        {
            var result = await Task.Run(
                    () => SetValue((AutomationElement)stored!, text), ct)
                .WaitAsync(UiaToolSupport.UiaTimeout, ct);
            await Task.Delay(100, ct);
            return result;
        }
        catch (TimeoutException)
        {
            return ToolResult.Fail(UiaToolSupport.TimeoutError);
        }
        catch (ElementNotAvailableException)
        {
            return ToolResult.Fail(UiaSnapshotStore.StaleError);
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"uia_set_value failed: {UiaToolSupport.Describe(ex)}");
        }
    }

    private static ToolResult SetValue(AutomationElement element, string text)
    {
        var current = element.Current; // liveness probe (stale → catch above)
        var name = current.Name ?? "";
        if (!current.IsEnabled)
            return ToolResult.Fail($"Element \"{name}\" is disabled.");

        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern))
        {
            var value = (ValuePattern)pattern;
            if (!value.Current.IsReadOnly)
            {
                value.SetValue(text);
                return ToolResult.Ok($"Set \"{name}\" to \"{Preview(text)}\" via ValuePattern.");
            }
        }

        // Fallback: focus + select-all + retype. The window must be foreground
        // for the keystrokes to land in the focused element.
        var hWnd = UiaToolSupport.WindowHandleOf(element);
        if (hWnd != IntPtr.Zero)
            Win32Input.ForceForegroundWindow(hWnd);
        element.SetFocus();
        Thread.Sleep(120);
        Win32Input.PressKey("Ctrl+A");
        Thread.Sleep(50);
        Win32Input.TypeText(text);
        return ToolResult.Ok($"Set \"{name}\" to \"{Preview(text)}\" via keyboard (focus + retype).");
    }

    private static string Preview(string text) =>
        text.Length > 60 ? text[..60] + "…" : text;
}
