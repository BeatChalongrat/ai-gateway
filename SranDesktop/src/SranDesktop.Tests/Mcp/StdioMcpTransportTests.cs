using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Remote;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Phase 1 security baseline: local MCP executables remain fail-closed until
/// bidirectional stdio is implemented inside the interactive AppContainer
/// worker. These tests prove there is no host-process fallback and no
/// environment forwarding.
/// </summary>
public sealed class StdioMcpTransportTests
{
    [Fact]
    public async Task Local_Stdio_Server_Is_Blocked_When_Isolated_Transport_Is_Unavailable()
    {
        var (command, args) = StdioEchoServer.CommandLine();
        await using var transport = new StdioMcpTransport(command, args);

        Func<Task> action = () => transport.SendRequestAsync("initialize", null);

        await action.Should().ThrowAsync<McpProtocolException>()
            .WithMessage("*isolated*no fallback*");
        transport.ProcessId.Should().BeNull();
    }

    [Fact]
    public async Task Requested_Environment_Is_Not_Forwarded_To_A_Child()
    {
        var (command, args) = StdioEchoServer.CommandLine();
        await using var transport = new StdioMcpTransport(
            command,
            args,
            new Dictionary<string, string>
            {
                ["SRAN_SYNTHETIC_MCP_SECRET"] = "must-not-enter-child"
            });

        Func<Task> action = () => transport.SendRequestAsync("debug/env", null);

        await action.Should().ThrowAsync<McpProtocolException>();
        transport.ProcessId.Should().BeNull();
    }

    [Fact]
    public async Task Cancellation_Is_Observed_Before_Any_Launch_Attempt()
    {
        await using var transport = new StdioMcpTransport("server.exe");
        using var cts = new CancellationTokenSource();
        cts.Cancel();

        Func<Task> action = () => transport.SendRequestAsync("initialize", null, cts.Token);

        await action.Should().ThrowAsync<OperationCanceledException>();
        transport.ProcessId.Should().BeNull();
    }

    [Fact]
    public async Task Disposed_Transport_Cannot_Start()
    {
        var transport = new StdioMcpTransport("server.exe");
        await transport.DisposeAsync();

        Func<Task> action = () => transport.SendRequestAsync("initialize", null);

        await action.Should().ThrowAsync<ObjectDisposedException>();
    }
}
