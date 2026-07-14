# SRAN Desktop — Agentic Build Prompt: Claude-Style Agent

## Overview

Transform SRAN Desktop from a chat client with an optional "Agent Mode" toggle into a seamless AI agent that **decides on its own** when to use tools — identical to how Claude works. The AI sees all available tools every turn and uses them when the task requires it, with no user-facing toggle.

Additionally, add **Computer Use** tools (screenshot + mouse/keyboard control) so the AI can operate any application on the user's desktop.

---

## Architecture Decision

**Use the gateway path (`:8080`) as the single unified path for all desktop messages.**

Rationale: The desktop client needs a richer tool set (file, shell, computer use, clipboard, screenshot) than what chat-edge-api provides. Rather than modifying the Go chat-edge-api to proxy tool calls back to the desktop, the desktop runs its own agent loop directly through the gateway — the same approach Claude desktop uses.

- **Gateway** (`:8080/v1/chat/completions`) — every message, tools always attached
- **chat-edge-api** (`:4000`) — still used for conversation CRUD, file upload, models list, folders, memory API, title generation
- **Desktop AgentOrchestrator** — handles the full tool loop client-side

This means the desktop's AgentOrchestrator replaces the chat-edge-api's tool loop for desktop users, while the web client continues using chat-edge-api's built-in tool handling.

---

## Part 1 — Remove Toggle, Unify to Always-Agent

### 1.1 Remove IsAgentMode

**Delete completely:**
- `ChatViewModel.IsAgentMode` property
- `UserSettings.AgentModeDefault` setting
- The "Enable Agent Mode by default" checkbox in `SettingsView.xaml`
- `RunChatTurnAsync()` method — merge its functionality into the agent path
- The `if (IsAgentMode)` branch in `SendMessageAsync()`

**Keep:**
- `RunAgentTurnAsync()` → rename to `RunTurnAsync()` — this is now THE path
- `AgentOrchestrator` → rename to `ChatOrchestrator` (it's no longer "agent-specific")
- `ToolExecutions` collection — but change how it's displayed (see Part 5)

### 1.2 Always Send Tools

In `ChatOrchestrator.RunAsync()`, always call `_toolHost.GetToolDefinitions()` and include them in every request. Set `tool_choice = "auto"` always. The model decides when to use tools — if the user asks "what is 1+1?", the model just answers without tools. If the user asks "read my report.docx and summarize it", the model calls `read_file`.

### 1.3 Enhanced System Prompt

Replace the current simple system prompt in `ChatViewModel.BuildGatewayMessages()` with a comprehensive prompt modeled on Claude's approach. The system prompt must:

```
You are SRAN Assistant, an AI desktop assistant with the ability to use tools to help users.

## Tool Use Philosophy
- You have access to desktop tools. Use them when the task genuinely requires them.
- For knowledge questions, conversation, creative writing — just respond directly, no tools needed.
- For tasks involving files, the screen, running commands, or interacting with applications — use the appropriate tools.
- You decide autonomously. Never ask "should I use tools?" — just use them when needed.

## Computer Use
When the user asks you to interact with an application:
1. Take a screenshot to see the current screen state
2. Analyze the screenshot to understand what's visible
3. Perform actions (click, type, scroll) based on what you see
4. Take another screenshot to verify the result
5. Repeat until the task is complete

IMPORTANT: Always take a screenshot BEFORE clicking. Never guess coordinates — always look first.
Coordinates are in pixels from the top-left corner of the screen.

## Safety Rules
- NEVER enter passwords, API keys, or financial credentials
- NEVER execute destructive commands (format, rm -rf, etc.) without explicit user confirmation
- NEVER send messages or emails on behalf of the user without asking first
- For file modifications: proceed if the user explicitly asked, otherwise confirm
- For shell commands: always show the user what you're about to run for write/delete operations
- Read operations (screenshots, reading files, listing directories) are generally safe to proceed

## Response Style
- Be concise and direct
- When using tools, don't narrate every step — just do it and report the result
- If a tool fails, explain what happened and try an alternative approach
- Show tool activity inline in the conversation naturally
```

### 1.4 Server-Side Tools via API

The desktop loses chat-edge-api's built-in tools (memory, web search, image gen). Re-implement the critical ones as desktop-side tools that call chat-edge-api APIs:

**Memory tools** (call chat-edge-api's memory endpoints):
```csharp
// save_memory → POST /api/v1/memory
// list_memories → GET /api/v1/memory
// These are simple HTTP calls to chat-edge-api
```

**Web search** (call chat-edge-api or a search API):
```csharp
// web_search → call the gateway's search endpoint or a direct search API
```

**Image generation** (call the gateway):
```csharp
// generate_image → POST to gateway with image gen model
```

If these are complex to re-implement, **defer them** — the desktop can function with file/shell/computer-use tools first, and add memory/search/image later. The priority is the core agent experience.

### 1.5 Auto-Title via chat-edge-api

Continue using chat-edge-api for auto-title generation after the first exchange. The current `_api.StreamMessageAsync()` call for title gen should remain unchanged — it's a separate concern from the main message flow.

---

## Part 2 — Computer Use Tools

Add 7 new tools for desktop control. These follow Claude's exact computer-use pattern.

### 2.1 Tool: `computer_screenshot`

Already exists as `screenshot` — enhance it:

```csharp
public sealed class ComputerScreenshotTool : IMcpTool
{
    public string Name => "computer_screenshot";
    public string Description => "Take a screenshot of the screen. Returns the image for visual analysis. Always use this before clicking to see what's on screen.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowOnce; // Read-only, low risk

    // Parameters: none required (captures full primary screen)
    // Optional: { "window_title": "string" } to capture a specific window

    public async Task<ToolResult> ExecuteAsync(JsonElement args, CancellationToken ct)
    {
        // 1. Capture primary screen (or specific window if window_title provided)
        // 2. Scale down to max 1920px wide (reduce token cost)
        // 3. Save to temp PNG
        // 4. Return: { "width": 1920, "height": 1080, "path": "...", "image": "data:image/png;base64,..." }
    }
}
```

**Implementation:**
- Use `Graphics.CopyFromScreen()` for full screen
- For window capture: `FindWindow()` + `PrintWindow()` via P/Invoke
- Scale to max 1920px wide with aspect ratio maintained (reduces tokens)
- Return both file path and base64 for the model to analyze

### 2.2 Tool: `computer_click`

```csharp
public sealed class ComputerClickTool : IMcpTool
{
    public string Name => "computer_click";
    public string Description => "Click the mouse at the specified screen coordinates. Always take a screenshot first to see what's on screen before clicking.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
    {
        "type": "object",
        "properties": {
            "x": { "type": "integer", "description": "X coordinate in pixels from left edge" },
            "y": { "type": "integer", "description": "Y coordinate in pixels from top edge" },
            "button": { "type": "string", "enum": ["left", "right", "middle"], "default": "left" },
            "click_type": { "type": "string", "enum": ["single", "double", "triple"], "default": "single" }
        },
        "required": ["x", "y"]
    }""");

    public async Task<ToolResult> ExecuteAsync(JsonElement args, CancellationToken ct)
    {
        var x = args.GetProperty("x").GetInt32();
        var y = args.GetProperty("y").GetInt32();
        var button = args.TryGetString("button") ?? "left";
        var clickType = args.TryGetString("click_type") ?? "single";

        // Validate coordinates are within screen bounds
        // Move cursor + click using SendInput (P/Invoke)
        // Small delay (50ms) after click for UI to respond
        // Return: "Clicked {button} at ({x}, {y})"
    }
}
```

**P/Invoke for mouse:**
```csharp
[DllImport("user32.dll")]
static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

[StructLayout(LayoutKind.Sequential)]
struct INPUT { public uint type; public MOUSEINPUT mi; }

[StructLayout(LayoutKind.Sequential)]
struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }

// Flags: MOUSEEVENTF_MOVE=1, ABSOLUTE=0x8000, LEFTDOWN=2, LEFTUP=4, RIGHTDOWN=8, RIGHTUP=16
// Convert pixel coords to absolute: x * 65535 / screenWidth, y * 65535 / screenHeight
```

### 2.3 Tool: `computer_type`

```csharp
public sealed class ComputerTypeTool : IMcpTool
{
    public string Name => "computer_type";
    public string Description => "Type text into whatever currently has keyboard focus. For keyboard shortcuts, use computer_key instead.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    // Parameters: { "text": "string (required)" }

    public async Task<ToolResult> ExecuteAsync(JsonElement args, CancellationToken ct)
    {
        var text = args.GetProperty("text").GetString();
        // Use SendInput with KEYBDINPUT for each character
        // OR use System.Windows.Forms.SendKeys (simpler but less reliable)
        // OR use InputSimulator NuGet package
        // Best: SendInput with Unicode flag (KEYEVENTF_UNICODE) for each char
        // Handle newlines by sending Enter key
        // Small delay between chars for reliability (5ms)
    }
}
```

### 2.4 Tool: `computer_key`

```csharp
public sealed class ComputerKeyTool : IMcpTool
{
    public string Name => "computer_key";
    public string Description => "Press a keyboard key or shortcut (e.g. 'Enter', 'Ctrl+C', 'Alt+Tab', 'Ctrl+Shift+S'). Use for keyboard shortcuts and special keys.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    // Parameters: { "keys": "string, e.g. 'Ctrl+C', 'Enter', 'Alt+F4'" }

    // Parse key string: split on "+", map to VK codes
    // Hold modifiers (Ctrl/Alt/Shift/Win), press key, release modifiers
    // Use SendInput with KEYBDINPUT
}
```

**Key mapping table** (essential subset):
```
Enter=0x0D, Tab=0x09, Escape=0x1B, Space=0x20, Backspace=0x08, Delete=0x2E
Up=0x26, Down=0x28, Left=0x25, Right=0x27, Home=0x24, End=0x23
PageUp=0x21, PageDown=0x22, F1-F12=0x70-0x7B
Ctrl=0xA2, Alt=0xA4, Shift=0xA0, Win=0x5B
A-Z=0x41-0x5A, 0-9=0x30-0x39
```

### 2.5 Tool: `computer_scroll`

```csharp
public sealed class ComputerScrollTool : IMcpTool
{
    public string Name => "computer_scroll";
    public string Description => "Scroll at the specified coordinates. Move the mouse to (x,y) first, then scroll.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowOnce;

    // Parameters: { "x": int, "y": int, "direction": "up"|"down"|"left"|"right", "amount": int (1-10, default 3) }

    // Move cursor to (x,y), then send MOUSEEVENTF_WHEEL with mouseData=120*amount (up) or -120*amount (down)
    // For horizontal: MOUSEEVENTF_HWHEEL
}
```

### 2.6 Tool: `computer_drag`

```csharp
public sealed class ComputerDragTool : IMcpTool
{
    public string Name => "computer_drag";
    public string Description => "Click and drag from one point to another.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    // Parameters: { "start_x", "start_y", "end_x", "end_y": int }

    // Move to start → mouse down → incremental move to end → mouse up
    // Use multiple small moves (not one jump) for reliability
}
```

### 2.7 Tool: `open_application`

Enhance the existing `open_url` or add a new tool:

```csharp
public sealed class OpenApplicationTool : IMcpTool
{
    public string Name => "open_application";
    public string Description => "Launch or bring to focus a Windows application by name.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    // Parameters: { "name": "string, e.g. 'Notepad', 'LINE', 'Chrome', 'Excel'" }

    // 1. Try FindWindow with known class names / window titles
    // 2. If found: SetForegroundWindow to bring to focus
    // 3. If not found: search Start Menu shortcuts, launch via Process.Start
    // 4. Wait up to 3 seconds for window to appear
    // 5. Return: "Opened {name}" or "Could not find {name}"
}
```

### 2.8 Native Helpers Module

Create `SranDesktop.Infrastructure/Native/Win32Input.cs`:

```csharp
internal static class Win32Input
{
    // All P/Invoke declarations for SendInput, FindWindow, SetForegroundWindow,
    // GetCursorPos, SetCursorPos, EnumWindows, GetWindowText, GetWindowRect,
    // keybd_event, mouse_event, etc.

    public static void MoveMouse(int x, int y) { ... }
    public static void LeftClick(int x, int y) { ... }
    public static void RightClick(int x, int y) { ... }
    public static void DoubleClick(int x, int y) { ... }
    public static void TypeText(string text) { ... }
    public static void PressKey(string keyCombo) { ... }
    public static void Scroll(int x, int y, int delta) { ... }
    public static void Drag(int x1, int y1, int x2, int y2) { ... }
    public static IntPtr FindAndFocusWindow(string titleOrProcess) { ... }
}
```

---

## Part 3 — Consent & Safety Model (Claude-Style)

### 3.1 Tiered Permission Defaults

Redesign tool permissions to match Claude's 3-tier model:

```csharp
public enum ToolRisk { ReadOnly, Moderate, Dangerous }
```

| Tool | Risk | Default | Rationale |
|------|------|---------|-----------|
| `computer_screenshot` | ReadOnly | AllowAlways | Just looking |
| `list_directory` | ReadOnly | AllowAlways | Just looking |
| `search_files` | ReadOnly | AllowAlways | Just looking |
| `read_file` | ReadOnly | AllowAlways | Just reading |
| `read_clipboard` | ReadOnly | AllowOnce | Privacy-sensitive read |
| `computer_scroll` | Moderate | AllowOnce | Low-risk interaction |
| `open_application` | Moderate | Ask | Launches apps |
| `open_url` | Moderate | Ask | Opens browser |
| `computer_click` | Moderate | Ask first time, then AllowOnce per app | Screen interaction |
| `computer_type` | Moderate | Ask | Types into apps |
| `computer_key` | Moderate | Ask | Keyboard shortcuts |
| `computer_drag` | Moderate | Ask | Screen interaction |
| `write_file` | Dangerous | Ask | Modifies files |
| `write_clipboard` | Dangerous | Ask | Modifies clipboard |
| `run_command` | Dangerous | Ask | Executes commands |

### 3.2 App-Level Consent (Claude's `request_access` pattern)

Add a new tool or consent flow: before the AI uses computer-use tools on a specific application, it should request access for that app. Implement this as:

1. First time AI wants to click/type in an app → consent dialog: "AI wants to interact with **[App Name]**. Allow?"
2. Options: Deny / Allow for this session / Always allow
3. Store per-app permissions in `PermissionService`
4. The AI should identify the target app from the screenshot before acting

### 3.3 Prohibited Actions

Hard-code these blocks (reject before even asking consent):
- `computer_type` with text containing patterns: passwords, API keys, credit card numbers
- `computer_key` with `Alt+F4` on system-critical processes
- `run_command` with blocklisted commands (already implemented)
- `write_file` to system directories outside `%USERPROFILE%`

### 3.4 Enhanced Consent Dialog

Update `ToolConsentDialog` to show:
- **Screenshot thumbnail** when the tool is a computer-use action (so user can see what the AI is about to click)
- **Coordinates highlighted** on the screenshot for click/type actions
- **Batch consent**: "Allow all computer actions for this app this session" option

---

## Part 4 — Tool Activity Display (Inline, Not Panel)

### 4.1 Remove Separate Agent Panel

Currently tool executions show in a collapsible `Expander` panel below the chat. Claude shows tool use **inline in the conversation** as collapsible blocks within the assistant message.

**Change to inline display:**

Each tool execution becomes a collapsible row inside the `MessageBubble` for the assistant message, shown between the reasoning section and the main content:

```
┌─ 🔧 read_file("report.docx")                    ✓ 120ms ─┐
│  [collapsed by default — click to expand]                    │
│  Content: "Q3 Financial Report..."                           │
└──────────────────────────────────────────────────────────────┘

┌─ 📸 computer_screenshot()                         ✓ 85ms  ─┐
│  [collapsed — click to see screenshot thumbnail]             │
└──────────────────────────────────────────────────────────────┘

Here's a summary of the report you asked me to review...
```

### 4.2 Streaming Tool Status

During tool execution, show a pulsing status in the message area:
- "🔍 Reading report.docx..." (while executing)
- "📸 Taking screenshot..." (while capturing)
- "⌨️ Typing in Notepad..." (while typing)

These replace the current Agent Activity panel.

### 4.3 XAML Structure

In `MessageBubble.xaml`, add a tool executions section above the content area in the assistant StackPanel:

```xml
<!-- Tool executions (inline, collapsible) -->
<ItemsControl ItemsSource="{Binding ToolExecutions}"
              Visibility="{Binding HasToolExecutions, Converter={StaticResource BoolToVisibility}}">
    <ItemsControl.ItemTemplate>
        <DataTemplate DataType="{x:Type vm:ToolExecutionItemViewModel}">
            <Border Background="{DynamicResource BgSecondaryBrush}"
                    CornerRadius="8" Margin="0,2" Padding="10,6">
                <Expander IsExpanded="False">
                    <Expander.Header>
                        <DockPanel>
                            <!-- Status icon (pulsing dot / check / x) -->
                            <!-- Tool name + args summary -->
                            <!-- Duration -->
                        </DockPanel>
                    </Expander.Header>
                    <!-- Expanded: full result text or screenshot thumbnail -->
                    <TextBlock Text="{Binding ResultPreview}" ... />
                </Expander>
            </Border>
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

---

## Part 5 — Message Flow (Unified)

### 5.1 New Flow Diagram

```
User types message
    │
    ▼
ChatViewModel.SendMessageAsync()
    │
    ▼
ChatOrchestrator.RunAsync(model, messages+tools, ct)
    │
    ├─ Build request: messages + ALL tool definitions + tool_choice="auto"
    ├─ System prompt includes tool policy (Part 1.3)
    │
    ▼
GatewayApiClient.StreamCompletionAsync()  ← gateway :8080
    │
    ├─ Stream content deltas → display in MessageBubble
    ├─ Stream reasoning → display in collapsible reasoning section
    │
    ▼
finish_reason == "tool_calls"?
    │
    ├─ NO → Done, display final message
    │
    ├─ YES:
    │   ├─ For each tool_call:
    │   │   ├─ Check PermissionService (3-tier)
    │   │   ├─ Show consent dialog if needed
    │   │   ├─ Execute tool locally
    │   │   ├─ Display inline tool activity in message
    │   │   └─ Append role:"tool" result to messages
    │   │
    │   └─ Loop back to gateway with updated messages
    │       (max 25 iterations, 5 min cap)
    │
    ▼
Display final assistant response
```

### 5.2 ChatOrchestrator Changes

```csharp
public sealed class ChatOrchestrator  // renamed from AgentOrchestrator
{
    private const int MaxIterations = 25;  // increased from 20
    private static readonly TimeSpan MaxDuration = TimeSpan.FromMinutes(10);  // increased

    // Same RunAsync pattern but ALWAYS used, no conditional
    public async IAsyncEnumerable<ChatStreamEvent> RunAsync(
        string model,
        List<GatewayMessage> messages,
        string conversationId,
        CancellationToken ct = default)
    {
        var tools = _toolHost.GetToolDefinitions();
        // ... same loop as current AgentOrchestrator.RunAsync()
    }
}
```

### 5.3 Image Handling in Tool Results

When `computer_screenshot` returns a base64 image, it should be sent to the model as a vision-capable message:

```csharp
// In ChatOrchestrator, when building the tool result message:
if (result.ContainsImage)
{
    toolResultMessage = new GatewayMessage
    {
        Role = "tool",
        ToolCallId = call.Id,
        Content = new object[]
        {
            new { type = "text", text = result.Text },
            new { type = "image_url", image_url = new { url = result.ImageDataUri } }
        }
    };
}
```

**IMPORTANT**: The model must support vision (Claude 3.5+, GPT-4o). Check `GatewayModels.cs` to ensure the `Content` field can be an array of content blocks (OpenAI multimodal format).

---

## Part 6 — File Changes Summary

### New Files
```
Infrastructure/Native/Win32Input.cs              — P/Invoke wrappers for mouse, keyboard, window management
Infrastructure/Mcp/Tools/ComputerClickTool.cs     — mouse click
Infrastructure/Mcp/Tools/ComputerTypeTool.cs      — type text
Infrastructure/Mcp/Tools/ComputerKeyTool.cs       — keyboard shortcuts
Infrastructure/Mcp/Tools/ComputerScrollTool.cs    — scroll wheel
Infrastructure/Mcp/Tools/ComputerDragTool.cs      — click and drag
Infrastructure/Mcp/Tools/OpenApplicationTool.cs   — launch/focus apps
```

### Modified Files
```
Infrastructure/Mcp/Tools/ScreenshotTool.cs        — rename to ComputerScreenshotTool, enhance with window capture + scaling
Infrastructure/Mcp/McpToolRegistry.cs              — no changes (tools registered externally)
App/Hosting/AppHostBuilder.cs                      — register new tools, remove IsAgentMode wiring
Application/Services/AgentOrchestrator.cs          — rename to ChatOrchestrator, always-on, increase caps
Application/Models/GatewayModels.cs                — support multimodal Content (array of content blocks)
Presentation/ViewModels/ChatViewModel.cs           — remove RunChatTurnAsync, remove IsAgentMode, single path
Presentation/ViewModels/MessageViewModel.cs        — add ToolExecutions collection for inline display
Presentation/Controls/MessageBubble.xaml           — add inline tool activity section
Presentation/Views/ChatView.xaml                   — remove Agent Activity panel (lines 81-168)
Presentation/Views/SettingsView.xaml               — remove "Agent Mode" checkbox
Presentation/ViewModels/SettingsViewModel.cs       — remove AgentModeDefault
Domain/Settings/UserSettings.cs                    — remove AgentModeDefault
```

### Files to Remove
```
(none — refactor, don't delete. Keep IChatService/ChatService for potential web-client reuse)
```

---

## Part 7 — Testing Checklist

### Functional Tests
1. **Basic chat**: Send "what is 1+1?" → AI responds directly, no tools used
2. **File read**: Send "read the file C:\Users\...\test.txt" → AI calls `read_file`, shows content
3. **Shell command**: Send "what's my IP address?" → AI calls `run_command` with `ipconfig`, shows result
4. **Computer use flow**: Send "open Notepad and type hello" →
   - AI calls `open_application("Notepad")`
   - AI calls `computer_screenshot()` to verify Notepad opened
   - AI calls `computer_type("hello")`
   - AI calls `computer_screenshot()` to verify
   - AI responds "Done, I've typed 'hello' in Notepad"
5. **Consent flow**: First computer_click → consent dialog appears → Allow Once → action proceeds
6. **Multi-tool chain**: "Create a file called test.txt with 'hello world', then open it in Notepad" → write_file + open_application + verify with screenshot
7. **Safety**: "Enter my password 'abc123' into the login form" → AI refuses
8. **No-tool fallback**: "Tell me a joke" → AI responds directly, zero tool calls

### Regression Tests
- Existing 98 tests still pass
- Conversation CRUD via chat-edge-api still works
- File upload still works
- Auto-title generation still works
- Theme switching still works
- All existing tools (read_file, write_file, etc.) still work

### Screenshots to Capture
```
agentic-1-basic-chat.png           — normal chat, no tools
agentic-2-file-read.png            — inline tool activity showing file read
agentic-3-computer-use.png         — screenshot tool result + click action
agentic-4-consent-dialog.png       — computer use consent dialog
agentic-5-multi-tool.png           — chain of tool calls shown inline
agentic-6-notepad-demo.png         — AI controlling Notepad end-to-end
```

---

## Implementation Order

1. **Win32Input.cs** — native helpers (foundation for everything)
2. **New computer tools** (7 tools) — implement and register
3. **Rename AgentOrchestrator → ChatOrchestrator** — remove toggle, always-on
4. **ChatViewModel refactor** — single RunTurnAsync path
5. **System prompt** — comprehensive tool policy
6. **GatewayModels multimodal** — support image content blocks in tool results
7. **Inline tool display** — move from panel to message bubbles
8. **Remove Agent panel** from ChatView.xaml
9. **Settings cleanup** — remove AgentModeDefault
10. **Test end-to-end** — all 8 functional test scenarios
