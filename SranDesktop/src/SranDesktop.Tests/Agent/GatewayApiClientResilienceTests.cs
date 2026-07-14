using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using FluentAssertions;
using Xunit;
using static SranDesktop.Tests.Agent.GatewayClientTestSupport;

namespace SranDesktop.Tests.Agent;

/// <summary>Idle watchdog + one-shot pre-first-frame retry behavior of GatewayApiClient.</summary>
public class GatewayApiClientResilienceTests
{
    private const string RateLimitBody =
        """{"error":{"code":"rate_limited","message":"Too many requests"}}""";

    private static HttpResponseMessage TooManyRequests(RetryConditionHeaderValue? retryAfter = null)
    {
        var response = JsonResponse((HttpStatusCode)429, RateLimitBody);
        if (retryAfter is not null)
            response.Headers.RetryAfter = retryAfter;
        return response;
    }

    [Fact]
    public async Task Stalled_Stream_Yields_Stream_Stalled_Instead_Of_Hanging()
    {
        var handler = new SequenceHandler();
        var stalling = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StreamContent(new StallingStream("data: hello\n\n"))
        };
        stalling.Content.Headers.ContentType = new MediaTypeHeaderValue("text/event-stream");
        handler.Enqueue(stalling);
        var client = CreateClient(handler, KeyProvider().Object,
            idleTimeout: TimeSpan.FromMilliseconds(200));

        var frames = await CollectAsync(client);

        frames.Should().HaveCount(2);
        frames[0].Data.Should().Be("hello");
        frames[1].Data.Should().Contain("stream_stalled");
    }

    [Fact]
    public async Task Connection_Failure_Before_First_Frame_Retries_Once()
    {
        var delays = new List<TimeSpan>();
        var handler = new SequenceHandler();
        handler.EnqueueThrow(new HttpRequestException("connection refused"));
        handler.Enqueue(SseResponse("hello", "[DONE]"));
        var client = CreateClient(handler, KeyProvider().Object,
            retryDelay: (d, _) => { delays.Add(d); return Task.CompletedTask; });

        var frames = await CollectAsync(client);

        frames.Select(f => f.Data).Should().Equal("hello", "[DONE]");
        handler.Sends.Should().Be(2);
        delays.Should().Equal(TimeSpan.FromSeconds(2));
    }

    [Fact]
    public async Task Connection_Failure_Twice_Yields_Unreachable_Error()
    {
        var handler = new SequenceHandler();
        handler.EnqueueThrow(new HttpRequestException("connection refused"));
        handler.EnqueueThrow(new HttpRequestException("connection refused"));
        var client = CreateClient(handler, KeyProvider().Object);

        var frames = await CollectAsync(client);

        frames.Should().ContainSingle().Which.Data.Should().Contain("gateway_unreachable");
        handler.Sends.Should().Be(2);
    }

    [Fact]
    public async Task RateLimit_Honors_RetryAfter_Delta_Then_Succeeds()
    {
        var delays = new List<TimeSpan>();
        var handler = new SequenceHandler();
        handler.Enqueue(TooManyRequests(new RetryConditionHeaderValue(TimeSpan.FromSeconds(1))));
        handler.Enqueue(SseResponse("hello", "[DONE]"));
        var client = CreateClient(handler, KeyProvider().Object,
            retryDelay: (d, _) => { delays.Add(d); return Task.CompletedTask; });

        var frames = await CollectAsync(client);

        frames.Select(f => f.Data).Should().Equal("hello", "[DONE]");
        handler.Sends.Should().Be(2);
        delays.Should().Equal(TimeSpan.FromSeconds(1));
    }

    [Fact]
    public async Task RateLimit_Parses_RetryAfter_HttpDate_Form()
    {
        var delays = new List<TimeSpan>();
        var handler = new SequenceHandler();
        handler.Enqueue(TooManyRequests(
            new RetryConditionHeaderValue(DateTimeOffset.UtcNow.AddSeconds(10))));
        handler.Enqueue(SseResponse("[DONE]"));
        var client = CreateClient(handler, KeyProvider().Object,
            retryDelay: (d, _) => { delays.Add(d); return Task.CompletedTask; });

        await CollectAsync(client);

        delays.Should().ContainSingle()
            .Which.Should().BeGreaterThan(TimeSpan.FromSeconds(5)).And.BeLessThanOrEqualTo(TimeSpan.FromSeconds(10));
    }

    [Fact]
    public async Task RateLimit_RetryAfter_Is_Capped_At_30_Seconds()
    {
        var delays = new List<TimeSpan>();
        var handler = new SequenceHandler();
        handler.Enqueue(TooManyRequests(new RetryConditionHeaderValue(TimeSpan.FromSeconds(300))));
        handler.Enqueue(SseResponse("[DONE]"));
        var client = CreateClient(handler, KeyProvider().Object,
            retryDelay: (d, _) => { delays.Add(d); return Task.CompletedTask; });

        await CollectAsync(client);

        delays.Should().Equal(TimeSpan.FromSeconds(30));
    }

    [Fact]
    public async Task RateLimit_Missing_RetryAfter_Falls_Back_To_2_Seconds()
    {
        var delays = new List<TimeSpan>();
        var handler = new SequenceHandler();
        handler.Enqueue(TooManyRequests());
        handler.Enqueue(SseResponse("[DONE]"));
        var client = CreateClient(handler, KeyProvider().Object,
            retryDelay: (d, _) => { delays.Add(d); return Task.CompletedTask; });

        await CollectAsync(client);

        delays.Should().Equal(TimeSpan.FromSeconds(2));
    }

    [Fact]
    public async Task RateLimit_On_Both_Attempts_Yields_Error_Body_After_Two_Sends()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(TooManyRequests(new RetryConditionHeaderValue(TimeSpan.FromSeconds(1))));
        handler.Enqueue(TooManyRequests(new RetryConditionHeaderValue(TimeSpan.FromSeconds(1))));
        var client = CreateClient(handler, KeyProvider().Object);

        var frames = await CollectAsync(client);

        frames.Should().ContainSingle().Which.Data.Should().Be(RateLimitBody);
        handler.Sends.Should().Be(2);
    }

    [Fact]
    public async Task Cancellation_During_Retry_Delay_Propagates()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(TooManyRequests()); // no Retry-After → 2s real delay
        var client = CreateClient(handler, KeyProvider().Object,
            retryDelay: Task.Delay);
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));

        var act = async () =>
        {
            await foreach (var _ in client.StreamCompletionAsync(CompletionRequest(), cts.Token)) { }
        };

        await act.Should().ThrowAsync<OperationCanceledException>();
        handler.Sends.Should().Be(1);
    }
}
