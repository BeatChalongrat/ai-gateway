using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class TokenEstimatorTests
{
    [Fact]
    public void String_Content_Counts_Quarter_Chars_Plus_Overhead()
    {
        var message = new GatewayMessage { Role = "user", Content = new string('a', 40) };

        TokenEstimator.EstimateMessage(message).Should().Be(40 / 4 + 4);
    }

    [Fact]
    public void Char_Count_Rounds_Up()
    {
        var message = new GatewayMessage { Role = "user", Content = "abcde" }; // 5 chars → 2 tokens

        TokenEstimator.EstimateMessage(message).Should().Be(2 + 4);
    }

    [Fact]
    public void Multimodal_With_Two_Images_Adds_Flat_Image_Cost()
    {
        var message = new GatewayMessage
        {
            Role = "user",
            Content = new object[]
            {
                new { type = "text", text = new string('a', 40) },
                new { type = "image_url", image_url = new { url = "data:image/png;base64,AAA" } },
                new { type = "image_url", image_url = new { url = "data:image/png;base64,BBB" } }
            }
        };

        TokenEstimator.EstimateMessage(message).Should().Be(10 + 1100 + 1100 + 4);
    }

    [Fact]
    public void Tool_Call_Name_And_Arguments_Are_Counted()
    {
        var message = new GatewayMessage
        {
            Role = "assistant",
            ToolCalls = new List<GatewayToolCall>
            {
                new()
                {
                    Id = "c1",
                    Function = new GatewayToolCallFunction
                    {
                        Name = "read_file",            // 9 chars
                        Arguments = """{"path":"x"}""" // 12 chars
                    }
                }
            }
        };

        // ceil(21 / 4) = 6, plus the per-message overhead; no content.
        TokenEstimator.EstimateMessage(message).Should().Be(6 + 4);
    }

    [Fact]
    public void Empty_Message_Costs_Only_Overhead()
    {
        TokenEstimator.EstimateMessage(new GatewayMessage { Role = "assistant" }).Should().Be(4);
    }

    [Fact]
    public void Tool_Role_Message_Counts_Content()
    {
        var message = new GatewayMessage
        {
            Role = "tool",
            ToolCallId = "c1",
            Name = "read_file",
            Content = new string('x', 100)
        };

        TokenEstimator.EstimateMessage(message).Should().Be(25 + 4);
    }

    [Fact]
    public void EstimateMessages_Sums_All()
    {
        var messages = new List<GatewayMessage>
        {
            new() { Role = "system", Content = new string('s', 8) },
            new() { Role = "user", Content = new string('u', 8) }
        };

        TokenEstimator.EstimateMessages(messages).Should().Be((2 + 4) * 2);
    }
}
