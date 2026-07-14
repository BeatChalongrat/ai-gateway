using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class ScreenshotPrunerTests
{
    private static GatewayMessage Screenshot(int n) =>
        ScreenshotMessage.Create("computer_screenshot", $"data:image/png;base64,IMG{n}");

    [Fact]
    public void Prunes_All_But_Latest_Two()
    {
        var messages = new List<GatewayMessage>
        {
            new() { Role = "system", Content = "sys" },
            new() { Role = "user", Content = "click the button" },
            Screenshot(1),
            new() { Role = "assistant", Content = "clicked" },
            Screenshot(2),
            Screenshot(3),
            new() { Role = "assistant", Content = "done" },
            Screenshot(4),
        };
        var countBefore = messages.Count;

        ScreenshotPruner.Prune(messages, keepLatest: 2).Should().BeTrue();

        messages.Should().HaveCount(countBefore); // replaced, never removed
        messages[2].Role.Should().Be("user");
        messages[2].Content.Should().Be(ScreenshotPruner.PrunedPlaceholder);
        messages[4].Role.Should().Be("user");
        messages[4].Content.Should().Be(ScreenshotPruner.PrunedPlaceholder);
        ScreenshotMessage.IsScreenshotMessage(messages[5]).Should().BeTrue(); // latest 2 kept
        ScreenshotMessage.IsScreenshotMessage(messages[7]).Should().BeTrue();
        messages[1].Content.Should().Be("click the button"); // other rows untouched
        messages[3].Content.Should().Be("clicked");
        messages[6].Content.Should().Be("done");
    }

    [Fact]
    public void Second_Call_Is_Idempotent()
    {
        var messages = new List<GatewayMessage>
        {
            Screenshot(1), Screenshot(2), Screenshot(3), Screenshot(4)
        };

        ScreenshotPruner.Prune(messages).Should().BeTrue();
        ScreenshotPruner.Prune(messages).Should().BeFalse();
    }

    [Fact]
    public void Under_Limit_Prunes_Nothing()
    {
        var messages = new List<GatewayMessage> { Screenshot(1), Screenshot(2) };

        ScreenshotPruner.Prune(messages, keepLatest: 2).Should().BeFalse();

        ScreenshotMessage.IsScreenshotMessage(messages[0]).Should().BeTrue();
        ScreenshotMessage.IsScreenshotMessage(messages[1]).Should().BeTrue();
    }

    [Fact]
    public void Non_Screenshot_Multimodal_User_Message_Is_Untouched()
    {
        // A user-attached image has no "[Screenshot from" marker.
        var attached = new GatewayMessage
        {
            Role = "user",
            Content = new object[]
            {
                new { type = "text", text = "look at this photo" },
                new { type = "image_url", image_url = new { url = "data:image/png;base64,USER" } }
            }
        };
        var messages = new List<GatewayMessage>
        {
            attached, Screenshot(1), Screenshot(2), Screenshot(3)
        };

        ScreenshotPruner.Prune(messages, keepLatest: 1).Should().BeTrue();

        messages[0].Should().BeSameAs(attached);
        messages[1].Content.Should().Be(ScreenshotPruner.PrunedPlaceholder);
        messages[2].Content.Should().Be(ScreenshotPruner.PrunedPlaceholder);
        ScreenshotMessage.IsScreenshotMessage(messages[3]).Should().BeTrue();
    }
}
