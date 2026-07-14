using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

public class ContextNudgeTests
{
    private static ContextInfoDto Info(
        bool compacted = false, int wouldSend = 10, int total = 10) => new(
        ConversationId: "c1",
        TotalBudget: 100_000,
        SystemPromptTokens: 2_000,
        OutputReserve: 8_000,
        ToolReserve: 1_000,
        MessagesBudget: 80_000,
        MessagesTotal: total,
        MessagesWouldSend: wouldSend,
        Compacted: compacted,
        ExtractionTriggered: false);

    [Fact]
    public void Short_Chat_Does_Not_Nudge()
    {
        ContextNudge.ShouldNudge(Info()).Should().BeFalse();
    }

    [Fact]
    public void Compacted_History_Nudges()
    {
        ContextNudge.ShouldNudge(Info(compacted: true)).Should().BeTrue();
    }

    [Fact]
    public void Dropped_Messages_Nudge()
    {
        ContextNudge.ShouldNudge(Info(wouldSend: 40, total: 60)).Should().BeTrue();
    }

    [Fact]
    public void Sending_Everything_Uncompacted_Does_Not_Nudge()
    {
        ContextNudge.ShouldNudge(Info(wouldSend: 60, total: 60)).Should().BeFalse();
    }
}
