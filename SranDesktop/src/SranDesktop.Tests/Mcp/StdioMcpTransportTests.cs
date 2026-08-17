using System.Text.Json;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Remote;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Phase 12 D: MCP stdio transport against a REAL child process (see
/// <see cref="StdioEchoServer"/>). Verifies the newline-delimited JSON-RPC
/// framing end-to-end — one JSON object per '\n'-terminated line, trailing
/// '\r' tolerated (the echo server writes \r\n), responses matched to
/// requests by id, and non-response messages (server notifications) skipped.
/// </summary>
public class StdioMcpTransportTests
{
    private static readonly TimeSpan TestTimeout = TimeSpan.FromSeconds(30);

    private static StdioMcpTransport CreateTransport()
    {
        var (command, args) = StdioEchoServer.CommandLine();
        return new StdioMcpTransport(command, args);
    }

    [Fact]
    public async Task Framing_RoundTrip_Newline_Delimited_Json()
    {
        await using var transport = CreateTransport();
        var client = new McpRemoteClient(transport);

        // initialize: the server writes an UNSOLICITED notification line
        // BEFORE the response — getting the right ServerName back proves the
        // reader skipped it and id-matched the real response.
        var init = await client.InitializeAsync().WaitAsync(TestTimeout);
        init.ServerName.Should().Be("stdio-echo");

        var tools = await client.ListToolsAsync().WaitAsync(TestTimeout);
        tools.Should().Contain(t => t.Name == "stdio_echo");

        using var args = JsonDocument.Parse("""{"text":"hello over stdio"}""");
        var result = await client.CallToolAsync("stdio_echo", args.RootElement).WaitAsync(TestTimeout);
        result.IsError.Should().BeFalse();
        result.Content.Should().Be("hello over stdio");
    }

    [Fact]
    public async Task JsonRpc_Error_Responses_Are_Surfaced()
    {
        await using var transport = CreateTransport();

        var response = await transport.SendRequestAsync("no/such/method", null)
            .WaitAsync(TestTimeout);

        response.Error.Should().NotBeNull();
        response.Error!.Value.GetProperty("code").GetInt32().Should().Be(-32601);
    }

    [Fact]
    public async Task Env_Vars_Reach_The_Child_Process()
    {
        var (command, args) = StdioEchoServer.CommandLine();
        await using var transport = new StdioMcpTransport(command, args,
            new Dictionary<string, string> { ["SRAN_STDIO_TEST_ENV"] = "env-value-42" });

        var response = await transport.SendRequestAsync("debug/env", null)
            .WaitAsync(TestTimeout);

        response.Result!.Value.GetProperty("value").GetString().Should().Be("env-value-42");
    }

    [Fact]
    public async Task Process_Exit_Fails_Pending_Call_And_Raises_Exited()
    {
        await using var transport = CreateTransport();
        var client = new McpRemoteClient(transport);
        await client.InitializeAsync().WaitAsync(TestTimeout);

        var exited = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        transport.Exited += (_, _) => exited.TrySetResult();

        // stdio_crash exits WITHOUT replying: the pending call must fail
        // rather than hang, and Exited must fire.
        using var args = JsonDocument.Parse("{}");
        await Assert.ThrowsAsync<McpProtocolException>(
            () => client.CallToolAsync("stdio_crash", args.RootElement).WaitAsync(TestTimeout));
        await exited.Task.WaitAsync(TestTimeout);

        // The transport is dead for good — later sends fail fast (no silent
        // respawn; a re-dial builds a fresh transport + MCP handshake).
        await Assert.ThrowsAsync<McpProtocolException>(
            () => transport.SendRequestAsync("tools/list", null).WaitAsync(TestTimeout));
    }

    [Fact]
    public async Task Unresponsive_Server_Times_Out_As_Protocol_Error()
    {
        // debug/hang never replies but the process stays alive — before the
        // per-request timeout this call would park forever and hang the turn.
        var (command, args) = StdioEchoServer.CommandLine();
        await using var transport = new StdioMcpTransport(command, args,
            requestTimeout: TimeSpan.FromMilliseconds(500));

        var ex = await Assert.ThrowsAsync<McpProtocolException>(
            () => transport.SendRequestAsync("debug/hang", null).WaitAsync(TestTimeout));
        ex.Message.Should().Contain("timed out");

        // The process is still alive — a later request on the same transport
        // still round-trips (timeout is per-request, not a connection kill).
        var response = await transport.SendRequestAsync("debug/env", null).WaitAsync(TestTimeout);
        response.Result.Should().NotBeNull();
    }

    [Fact]
    public async Task Spawn_Failure_Is_A_Protocol_Error_Not_A_Crash()
    {
        await using var transport = new StdioMcpTransport("sran-no-such-mcp-server-xyz.exe");

        // Normalized to McpProtocolException — the exception type every
        // connector call site already maps to Disconnected.
        await Assert.ThrowsAsync<McpProtocolException>(
            () => transport.SendRequestAsync("initialize", null).WaitAsync(TestTimeout));
    }
}
