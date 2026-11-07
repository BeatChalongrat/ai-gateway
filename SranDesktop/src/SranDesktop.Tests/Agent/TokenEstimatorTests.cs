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
    public void Latin_Char_Count_Floors_Like_The_Server()
    {
        // Was ceil(5/4) = 2 before the server port; the shared model floors
        // (token_estimator.go:41) and leans on the +4 framing for slack.
        var message = new GatewayMessage { Role = "user", Content = "abcde" };

        TokenEstimator.EstimateMessage(message).Should().Be(1 + 4);
    }

    [Fact]
    public void Thai_Content_Costs_The_Dense_Script_Rate()
    {
        // 27 Thai runes + 1 space = 18 tokens (see the shared corpus).
        var message = new GatewayMessage
        {
            Role = "user",
            Content = "สวัสดีครับ ยินดีที่ได้รู้จัก"
        };

        TokenEstimator.EstimateMessage(message).Should().Be(18 + 4);
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

        // Server shape (token_estimator.go:88): name and arguments are
        // estimated separately and the call envelope adds a flat 8 —
        // floor(9/4) = 2, floor(12/4) = 3, +8, plus per-message overhead.
        TokenEstimator.EstimateMessage(message).Should().Be(2 + 3 + 8 + 4);
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

        // 100 latin chars = 25, plus the function name the wire carries
        // (floor(9/4) = 2 — the server counts it, we used to not).
        TokenEstimator.EstimateMessage(message).Should().Be(25 + 2 + 4);
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
