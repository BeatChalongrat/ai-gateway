using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Models;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class GatewayChunkParserTests
{
    private readonly GatewayChunkParser _parser = new();

    [Fact]
    public void Content_Delta_Is_Emitted()
    {
        var events = _parser.Parse(
            """{"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}""");

        events.Should().ContainSingle().Which.Should().Be(new AgentContentDeltaEvent("Hello"));
    }

    [Fact]
    public void Fragmented_Tool_Call_Arguments_Accumulate()
    {
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}""");
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":\"/tmp/f"}}]},"finish_reason":null}]}""");
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"oo.txt\"}"}}]},"finish_reason":null}]}""");
        var events = _parser.Parse(
            """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""");

        var toolCalls = events.Should().ContainSingle()
            .Which.Should().BeOfType<AgentToolCallsEvent>().Subject.ToolCalls;
        toolCalls.Should().ContainSingle();
        toolCalls[0].Id.Should().Be("call_1");
        toolCalls[0].Function.Name.Should().Be("read_file");
        toolCalls[0].Function.Arguments.Should().Be("""{"path":"/tmp/foo.txt"}""");
    }

    [Fact]
    public void Dlp_Buffered_SingleShot_Arguments_Work()
    {
        // Header with empty args, then ONE frame with the complete arguments.
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_a","type":"function","function":{"name":"list_directory","arguments":""}}]},"finish_reason":null}]}""");
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":\"C:\\\\data\"}"}}]},"finish_reason":null}]}""");
        var events = _parser.Parse(
            """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""");

        var call = events.OfType<AgentToolCallsEvent>().Single().ToolCalls.Single();
        call.Function.Arguments.Should().Be("""{"path":"C:\\data"}""");
    }

    [Fact]
    public void Multiple_Parallel_Tool_Calls_Are_Indexed()
    {
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c0","function":{"name":"read_file","arguments":"{\"path\":\"a\"}"}},{"index":1,"id":"c1","function":{"name":"list_directory","arguments":"{\"path\":\"b\"}"}}]},"finish_reason":null}]}""");
        var events = _parser.Parse(
            """{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}""");

        var calls = events.OfType<AgentToolCallsEvent>().Single().ToolCalls;
        calls.Should().HaveCount(2);
        calls[0].Function.Name.Should().Be("read_file");
        calls[1].Function.Name.Should().Be("list_directory");
    }

    [Fact]
    public void Finish_Stop_Emits_Done()
    {
        var events = _parser.Parse(
            """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""");

        events.Should().ContainSingle().Which.Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public void Done_Sentinel_Emits_Done()
    {
        _parser.Parse("[DONE]").Should().ContainSingle()
            .Which.Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public void Error_Envelope_Emits_Error_With_Code()
    {
        var events = _parser.Parse(
            """{"error":{"code":"authentication_error","message":"Invalid token"}}""");

        events.Should().ContainSingle()
            .Which.Should().Be(new AgentErrorEvent("Invalid token", "authentication_error"));
    }

    [Fact]
    public void Error_Envelope_Without_Code_Has_Null_Code()
    {
        var events = _parser.Parse("""{"error":{"message":"boom"}}""");

        events.Should().ContainSingle().Which.Should().Be(new AgentErrorEvent("boom"));
    }

    [Fact]
    public void Context_Length_Code_Is_Extracted()
    {
        var events = _parser.Parse(
            """{"error":{"code":"context_length_exceeded","message":"too long"}}""");

        events.OfType<AgentErrorEvent>().Single().Code.Should().Be("context_length_exceeded");
    }

    [Fact]
    public void Upstream_Bad_Response_With_Context_Details_Is_Normalized()
    {
        // Old gateway without the typed error: the provider 400 body rides
        // inside error.details of a generic upstream_bad_response.
        var events = _parser.Parse(
            """{"error":{"code":"upstream_bad_response","message":"Provider 'openai' rejected request (400).","details":{"provider":"openai","status":400,"body":{"error":{"message":"This model's maximum context length is 128000 tokens."}}}}}""");

        events.OfType<AgentErrorEvent>().Single().Code.Should().Be("context_length_exceeded");
    }

    [Fact]
    public void Upstream_Bad_Response_With_Unrelated_Details_Keeps_Its_Code()
    {
        var events = _parser.Parse(
            """{"error":{"code":"upstream_bad_response","message":"Provider 'openai' rejected request (400).","details":{"provider":"openai","status":400,"body":{"error":{"message":"Unknown parameter: foo"}}}}}""");

        events.OfType<AgentErrorEvent>().Single().Code.Should().Be("upstream_bad_response");
    }

    [Fact]
    public void Malformed_Frame_Is_Skipped()
    {
        _parser.Parse("not json").Should().BeEmpty();
    }

    [Fact]
    public void Fallback_Info_Frame_Emits_Event()
    {
        var events = _parser.Parse(
            """{"type":"fallback_info","fallback_info":{"requested_model":"gpt-4o","actual_model":"claude-sonnet-5","reason":"provider_unavailable","attempts":[{"provider":"openai"}]}}""");

        events.Should().ContainSingle().Which.Should().Be(
            new AgentFallbackEvent("gpt-4o", "claude-sonnet-5", "provider_unavailable"));
    }

    [Fact]
    public void Fallback_Info_With_Missing_Fields_Does_Not_Throw()
    {
        _parser.Parse("""{"type":"fallback_info","fallback_info":{"reason":"x"}}""")
            .Should().BeEmpty(); // unusable without both model names — ignored
        _parser.Parse("""{"type":"fallback_info"}""").Should().BeEmpty();
        _parser.Parse("""{"type":"fallback_info","fallback_info":"not an object"}""")
            .Should().BeEmpty();
    }

    [Fact]
    public void Reasoning_Notice_Frame_Emits_Event()
    {
        var events = _parser.Parse(
            """{"id":"r1","type":"reasoning_notice","created":1,"model":"gpt-4o-mini","reasoning_downgraded":true,"reason":"not reasoning-capable"}""");

        events.Should().ContainSingle().Which.Should().Be(
            new AgentReasoningNoticeEvent("gpt-4o-mini", "not reasoning-capable"));
    }

    [Fact]
    public void Other_Typed_Frames_Still_Yield_Nothing()
    {
        // analysis_status graduated from "ignored" to a typed event in the
        // reasoning-parity work (ReasoningParityTests pins the new shape),
        // and reasoning_notice graduated in the downgrade-notice work
        // (Reasoning_Notice_Frame_Emits_Event pins it); genuinely unknown
        // frames keep being ignored.
        _parser.Parse("""{"type":"unknown_future_frame","data":{}}""").Should().BeEmpty();
    }

    [Fact]
    public void Finish_Stop_With_Accumulated_Calls_Still_Emits_ToolCalls()
    {
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c0","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":null}]}""");
        var events = _parser.Parse(
            """{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}""");

        events.Should().ContainSingle().Which.Should().BeOfType<AgentToolCallsEvent>();
    }

    [Fact]
    public void Done_With_Accumulated_Calls_Emits_ToolCalls_Then_Done()
    {
        // Complete tool call but the provider never sends a finish_reason.
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"a\"}"}}]},"finish_reason":null}]}""");
        var events = _parser.Parse("[DONE]");

        events.Should().HaveCount(2);
        var call = events[0].Should().BeOfType<AgentToolCallsEvent>().Subject.ToolCalls.Single();
        call.Id.Should().Be("call_1");
        call.Function.Name.Should().Be("read_file");
        call.Function.Arguments.Should().Be("""{"path":"a"}""");
        events[1].Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public void Done_With_Nothing_Accumulated_Emits_Only_Done()
    {
        var events = _parser.Parse("[DONE]");

        events.Should().ContainSingle().Which.Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public void Done_Skips_Accumulator_Without_Name()
    {
        // Arguments arrived but the name never did — not an executable call.
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":\"a\"}"}}]},"finish_reason":null}]}""");
        var events = _parser.Parse("[DONE]");

        events.Should().ContainSingle().Which.Should().BeOfType<AgentStreamDoneEvent>();
    }

    [Fact]
    public void DrainRemaining_Returns_Pending_Calls_Exactly_Once()
    {
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":null}]}""");

        var first = _parser.DrainRemaining();
        first.Should().ContainSingle().Which.Function.Name.Should().Be("read_file");
        _parser.DrainRemaining().Should().BeEmpty();
    }

    [Fact]
    public void DrainRemaining_Skips_Accumulator_Without_Name()
    {
        _parser.Parse(
            """{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":\"a\"}"}}]},"finish_reason":null}]}""");

        _parser.DrainRemaining().Should().BeEmpty();
    }
}
