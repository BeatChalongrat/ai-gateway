using System.Collections.Generic;
using SranDesktop.Application.Models;
using SranDesktop.Shell.Bridge;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>P1 (cancel loses work): a turn cancelled mid-tool leaves an
/// assistant tool_call with no matching role:"tool" answer. Persisting that
/// unbalanced chain makes the next turn's replay fail at the gateway, so the
/// coordinator synthesises a "cancelled" tool result for each dangling call
/// before persisting. These pin that balancing.</summary>
public class CancelledTurnBalancingTests
{
    private static GatewayMessage AssistantWithCall(string id, string name) => new()
    {
        Role = "assistant",
        Content = null,
        ToolCalls = new List<GatewayToolCall>
        {
            new() { Id = id, Function = new GatewayToolCallFunction { Name = name, Arguments = "{}" } },
        },
    };

    private static GatewayMessage ToolResult(string callId, string name) => new()
    {
        Role = "tool", ToolCallId = callId, Name = name, Content = "done",
    };

    [Fact]
    public void DanglingToolCall_GetsSyntheticCancelledResult()
    {
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "ping 20 times" },
            AssistantWithCall("call_1", "run_command"),
            // cancelled mid-run: no tool result for call_1
        };
        var chainStart = 1;

        ShellTurnCoordinator.AppendCancelledToolResults(messages, chainStart);

        var toolRows = messages.FindAll(m => m.Role == "tool");
        Assert.Single(toolRows);
        Assert.Equal("call_1", toolRows[0].ToolCallId);
        Assert.Equal("run_command", toolRows[0].Name);
        Assert.Contains("cancelled", toolRows[0].Content as string ?? "");
    }

    [Fact]
    public void BalancedChain_IsLeftUnchanged()
    {
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "do it" },
            AssistantWithCall("call_1", "run_command"),
            ToolResult("call_1", "run_command"),
        };
        var before = messages.Count;

        ShellTurnCoordinator.AppendCancelledToolResults(messages, chainStart: 1);

        Assert.Equal(before, messages.Count); // no dangling call → no synthetic row
    }

    [Fact]
    public void OnlyTheUnansweredCall_IsSynthesised()
    {
        var messages = new List<GatewayMessage>
        {
            new() { Role = "user", Content = "two tools" },
            new()
            {
                Role = "assistant", Content = null,
                ToolCalls = new List<GatewayToolCall>
                {
                    new() { Id = "a", Function = new GatewayToolCallFunction { Name = "read_file", Arguments = "{}" } },
                    new() { Id = "b", Function = new GatewayToolCallFunction { Name = "run_command", Arguments = "{}" } },
                },
            },
            ToolResult("a", "read_file"), // 'a' answered, 'b' dangling
        };

        ShellTurnCoordinator.AppendCancelledToolResults(messages, chainStart: 1);

        var synthesised = messages.FindAll(m => m.Role == "tool" && (m.Content as string ?? "").Contains("cancelled"));
        Assert.Single(synthesised);
        Assert.Equal("b", synthesised[0].ToolCallId);
    }
}
