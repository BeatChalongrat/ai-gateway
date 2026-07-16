using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase A PART 5: model-maintained plan tool + parser, and its
/// survival across the durable tool chain (PART 1).</summary>
public class PlanToolTests
{
    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    [Fact]
    public void Parser_Reads_Steps_And_Statuses()
    {
        var steps = PlanParser.Parse(JsonSerializer.Serialize(new
        {
            steps = new[]
            {
                new { step = "Read Program.cs", status = "done" },
                new { step = "Add method", status = "in_progress" },
                new { step = "Build", status = "pending" },
            }
        }));

        steps.Should().HaveCount(3);
        steps[0].Status.Should().Be(PlanStep.Done);
        steps[1].Status.Should().Be(PlanStep.InProgress);
        steps[2].Status.Should().Be(PlanStep.Pending);
    }

    [Theory]
    [InlineData("in-progress", "in_progress")]
    [InlineData("completed", "done")]
    [InlineData("todo", "pending")]
    [InlineData(null, "pending")]
    public void Parser_Normalizes_Status_Aliases(string? raw, string expected)
    {
        var steps = PlanParser.Parse(JsonSerializer.Serialize(new { steps = new[] { new { step = "x", status = raw } } }));
        steps.Should().ContainSingle().Which.Status.Should().Be(expected);
    }

    [Fact]
    public void Parser_Ignores_Malformed_Input()
    {
        PlanParser.Parse("not json").Should().BeEmpty();
        PlanParser.Parse("{}").Should().BeEmpty();
        PlanParser.Parse(JsonSerializer.Serialize(new { steps = new[] { new { status = "done" } } }))
            .Should().BeEmpty(); // missing step text
    }

    [Fact]
    public async Task UpdatePlan_Tool_Is_Side_Effect_Free_And_Confirms()
    {
        var tool = new UpdatePlanTool();
        tool.DefaultPermission.Should().Be(PermissionLevel.AllowAlways);

        var result = await tool.ExecuteAsync(
            Args(new { steps = new[] { new { step = "a", status = "done" }, new { step = "b", status = "in_progress" } } }),
            CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("2 steps");
        result.Content.Should().Contain("1 done");
    }

    [Fact]
    public void Plan_Survives_The_Durable_Tool_Chain()
    {
        // An update_plan call rides the assistant tool-call chain, so PART 1
        // persistence carries it — and it re-parses to the same plan on reload.
        var planArgs = JsonSerializer.Serialize(new
        {
            steps = new[] { new { step = "step one", status = "done" }, new { step = "step two", status = "pending" } }
        });
        var chain = new List<GatewayMessage>
        {
            new()
            {
                Role = "assistant",
                ToolCalls = new List<GatewayToolCall>
                {
                    new() { Id = "p1", Function = new GatewayToolCallFunction { Name = "update_plan", Arguments = planArgs } }
                }
            },
            new() { Role = "tool", Content = "Plan updated", ToolCallId = "p1", Name = "update_plan" },
        };

        var rows = AgentChainCodec.ToRows("c1", "asst1", AgentChainCodec.Sanitize(chain), DateTimeOffset.UtcNow, 1);
        rows.Add(new Domain.Entities.Message
        {
            Id = "asst1", ConversationId = "c1", Role = SranDesktop.Domain.Enums.MessageRole.Assistant,
            Content = "done", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 9
        });

        var regrouped = AgentChainCodec.GroupChains(rows)["asst1"];
        var recoveredArgs = regrouped
            .Where(m => m.Role == "assistant" && m.ToolCalls is { Count: > 0 })
            .SelectMany(m => m.ToolCalls!)
            .Last(c => c.Function.Name == "update_plan")
            .Function.Arguments;

        var steps = PlanParser.Parse(recoveredArgs);
        steps.Should().HaveCount(2);
        steps[0].Should().Be(new PlanStep("step one", PlanStep.Done));
    }
}
