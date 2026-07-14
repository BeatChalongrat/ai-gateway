using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class ContextBudgeterTests
{
    private static GatewayMessage User(string text) => new() { Role = "user", Content = text };
    private static GatewayMessage Assistant(string text) => new() { Role = "assistant", Content = text };
    private static GatewayMessage System_(string text) => new() { Role = "system", Content = text };

    /// <summary>system(100 chars) + N groups of [user(400) + assistant(400)] —
    /// 29 + N·208 estimated tokens with the estimator's 4-chars/token rule.</summary>
    private static List<GatewayMessage> History(int groups)
    {
        var messages = new List<GatewayMessage> { System_(new string('s', 100)) };
        for (var i = 1; i <= groups; i++)
        {
            messages.Add(User($"u{i}:" + new string('u', 400 - $"u{i}:".Length)));
            messages.Add(Assistant($"a{i}:" + new string('a', 400 - $"a{i}:".Length)));
        }
        return messages;
    }

    private static bool IsTrimNote(GatewayMessage m) =>
        m is { Role: "user", Content: string text } && text.StartsWith("[Note: ");

    [Fact]
    public void Under_Budget_Is_Untouched()
    {
        var messages = History(2);
        var snapshot = messages.ToList();

        // budget = 10000 * 0.9 = 9000, estimate ≈ 445
        var result = ContextBudgeter.FitToBudget(messages, 10_000, outputReserve: 0);

        result.Trimmed.Should().BeFalse();
        result.OmittedMessages.Should().Be(0);
        messages.Should().Equal(snapshot);
    }

    [Fact]
    public void Over_Budget_Removes_Oldest_Groups_And_Inserts_One_Note()
    {
        // estimate = 29 + 6·208 = 1277 > budget 900 → remove g1 (1069) and g2 (861).
        var messages = History(6);

        var result = ContextBudgeter.FitToBudget(messages, 1000, outputReserve: 0);

        result.Trimmed.Should().BeTrue();
        result.OmittedMessages.Should().Be(4);
        messages[0].Role.Should().Be("system");
        IsTrimNote(messages[1]).Should().BeTrue();
        ((string)messages[1].Content!).Should().Contain("4 earlier messages");
        messages.Count(IsTrimNote).Should().Be(1);
        // Oldest two groups gone, the rest (incl. the final group) intact.
        messages.Should().HaveCount(1 + 1 + 8);
        ((string)messages[2].Content!).Should().StartWith("u3:");
        ((string)messages[^2].Content!).Should().StartWith("u6:");
        ((string)messages[^1].Content!).Should().StartWith("a6:");
    }

    [Fact]
    public void Final_Group_Is_Never_Removed()
    {
        // One giant group that alone exceeds the budget.
        var messages = new List<GatewayMessage>
        {
            System_("sys"),
            User(new string('u', 4000)),
            Assistant(new string('a', 4000)),
        };

        var result = ContextBudgeter.FitToBudget(messages, 1000, outputReserve: 0);

        messages.Should().HaveCount(3); // nothing removed
        ((string)messages[1].Content!).Should().HaveLength(4000);
        result.EstimatedTokens.Should().BeGreaterThan(900); // sent anyway; retry path handles a reject
    }

    [Fact]
    public void Tool_Call_Group_Is_Removed_Atomically()
    {
        // Group 1 carries an assistant tool_call + its tool result — naive
        // per-message trimming could orphan the tool message.
        var messages = new List<GatewayMessage>
        {
            System_("sys"),
            User("u1: do the thing " + new string('u', 400)),
            new()
            {
                Role = "assistant",
                ToolCalls = new List<GatewayToolCall>
                {
                    new()
                    {
                        Id = "call_1",
                        Function = new GatewayToolCallFunction
                            { Name = "read_file", Arguments = """{"path":"x"}""" }
                    }
                }
            },
            new() { Role = "tool", ToolCallId = "call_1", Name = "read_file", Content = new string('t', 2000) },
            Assistant("a1: done " + new string('a', 400)),
            User("u2: final question"),
            Assistant("a2: final answer"),
        };

        var result = ContextBudgeter.FitToBudget(messages, 500, outputReserve: 0);

        result.Trimmed.Should().BeTrue();
        result.OmittedMessages.Should().Be(4); // the whole first group
        messages.Should().NotContain(m => m.Role == "tool");
        messages.Should().NotContain(m => m.ToolCalls != null);
        ((string)messages[^2].Content!).Should().StartWith("u2:");
        ((string)messages[^1].Content!).Should().StartWith("a2:");
    }

    [Fact]
    public void Monster_Final_Group_Falls_Back_To_Screenshot_Pruning()
    {
        var messages = new List<GatewayMessage>
        {
            System_("sys"),
            User("click through the wizard"),
            ScreenshotMessage.Create("computer_screenshot", "data:image/png;base64,ONE"),
            Assistant("clicked next"),
            ScreenshotMessage.Create("computer_screenshot", "data:image/png;base64,TWO"),
            ScreenshotMessage.Create("computer_screenshot", "data:image/png;base64,THREE"),
        };
        var countBefore = messages.Count;

        // Single group; three ~1100-token screenshots blow a 900-token budget.
        var result = ContextBudgeter.FitToBudget(messages, 1000, outputReserve: 0);

        result.Trimmed.Should().BeTrue();
        result.OmittedMessages.Should().Be(0);
        messages.Should().HaveCount(countBefore); // pruned in place, never dropped
        messages.Count(IsTrimNote).Should().Be(0);
        messages[2].Content.Should().Be(ScreenshotPruner.PrunedPlaceholder);
        messages[4].Content.Should().Be(ScreenshotPruner.PrunedPlaceholder);
        ScreenshotMessage.IsScreenshotMessage(messages[5]).Should().BeTrue(); // newest kept
    }

    [Fact]
    public void Retrim_Merges_Into_A_Single_Cumulative_Note()
    {
        var messages = History(8);
        var first = ContextBudgeter.FitToBudget(messages, 1000, outputReserve: 0);
        first.Trimmed.Should().BeTrue();

        // Halved budget (mirrors the orchestrator's context-error recovery).
        var second = ContextBudgeter.FitToBudget(messages, 500, outputReserve: 0);

        second.Trimmed.Should().BeTrue();
        second.OmittedMessages.Should().BeGreaterThan(first.OmittedMessages);
        messages.Count(IsTrimNote).Should().Be(1);
        ((string)messages[1].Content!).Should().Contain($"{second.OmittedMessages} earlier messages");
        // Final group always survives.
        ((string)messages[^2].Content!).Should().StartWith("u8:");
        ((string)messages[^1].Content!).Should().StartWith("a8:");
    }
}
