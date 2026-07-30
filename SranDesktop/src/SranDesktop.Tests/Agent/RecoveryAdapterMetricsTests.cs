using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class RecoveryAdapterMetricsTests
{
    [Fact]
    public void Recovery_Switches_Strategy_And_Stops_At_Limit()
    {
        RecoveryCoordinator.Next("a", 0).Strategy
            .Should().Be(RecoveryStrategy.RefreshSemanticTree);
        RecoveryCoordinator.Next("a", 3).Strategy
            .Should().Be(RecoveryStrategy.FreshWindowVisionCapture);
        RecoveryCoordinator.Next("a", 4).Strategy
            .Should().Be(RecoveryStrategy.SafeStop);
        RecoveryCoordinator.Next("a", 4).MayUseRawPixel.Should().BeFalse();
    }

    [Fact]
    public void Discord_Does_Not_Toggle_When_State_Is_Already_Correct()
    {
        var adapter = new DiscordApplicationAdapter();
        var decision = adapter.ResolveMicrophoneState(
            new DiscordObservation(true, 10, 42, true, true, true), true);

        decision.Execute.Should().BeFalse();
        decision.TaskCompleted.Should().BeTrue();
    }

    [Fact]
    public void Discord_Uses_Uia_Toggle_And_Requires_Post_Verification()
    {
        var adapter = new DiscordApplicationAdapter();
        var decision = adapter.ResolveMicrophoneState(
            new DiscordObservation(true, 10, 42, false, true, true), true);

        decision.Execute.Should().BeTrue();
        decision.Method.Should().Be(TargetMethod.WindowsUia);
        decision.TaskCompleted.Should().BeFalse();
        adapter.VerifyMicrophoneEffect(
            true, new DiscordObservation(true, 10, 42, true, true, true))
            .Should().BeTrue();
    }

    [Fact]
    public void Discord_Action_Not_Verified_Is_Not_Complete()
    {
        new DiscordApplicationAdapter().VerifyMicrophoneEffect(
            true, new DiscordObservation(true, 10, 42, false, true, true))
            .Should().BeFalse();
    }

    [Fact]
    public void Production_Gate_Does_Not_Lower_Thresholds()
    {
        ProductionGate.Passes(new ProductionGateInput(
            0, 0, .99, .98, .949, .95, .95, true)).Should().BeFalse();
    }
}
