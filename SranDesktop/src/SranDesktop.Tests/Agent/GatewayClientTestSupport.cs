using System.Net;
using System.Net.Http;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Api;

namespace SranDesktop.Tests.Agent;

/// <summary>Shared fakes for GatewayApiClient auth/resilience tests — no real network.</summary>
internal static class GatewayClientTestSupport
{
    /// <summary>Replays a scripted sequence of responses (or exceptions) and
    /// records every send's Bearer token.</summary>
    internal sealed class SequenceHandler : HttpMessageHandler
    {
        private readonly Queue<Func<HttpResponseMessage>> _responses = new();

        public int Sends { get; private set; }
        public List<string?> BearerTokens { get; } = new();

        public void Enqueue(HttpResponseMessage response) => _responses.Enqueue(() => response);
        public void EnqueueThrow(Exception exception) => _responses.Enqueue(() => throw exception);

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Sends++;
            BearerTokens.Add(request.Headers.Authorization?.Parameter);
            return Task.FromResult(_responses.Dequeue()());
        }
    }

    /// <summary>Serves its initial bytes, then stalls forever (until cancelled) —
    /// simulates a server that keeps the socket open but sends nothing.</summary>
    internal sealed class StallingStream : Stream
    {
        private ReadOnlyMemory<byte> _pending;

        public StallingStream(string initial) => _pending = Encoding.UTF8.GetBytes(initial);

        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default)
        {
            if (!_pending.IsEmpty)
            {
                var n = Math.Min(buffer.Length, _pending.Length);
                _pending[..n].CopyTo(buffer);
                _pending = _pending[n..];
                return n;
            }
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return 0;
        }

        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position
        {
            get => throw new NotSupportedException();
            set => throw new NotSupportedException();
        }
        public override void Flush() { }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    public static HttpResponseMessage SseResponse(params string[] payloads) => new(HttpStatusCode.OK)
    {
        Content = new StringContent(
            string.Concat(payloads.Select(p => $"data: {p}\n\n")),
            Encoding.UTF8, "text/event-stream")
    };

    public static HttpResponseMessage JsonResponse(HttpStatusCode status, string body) => new(status)
    {
        Content = new StringContent(body, Encoding.UTF8, "application/json")
    };

    public static Mock<IAgentKeyProvider> KeyProvider(string? reprovisionedKey = "gw_new")
    {
        var keys = new Mock<IAgentKeyProvider>();
        keys.Setup(k => k.GetOrProvisionKeyAsync(It.IsAny<CancellationToken>())).ReturnsAsync("gw_old");
        keys.Setup(k => k.ReprovisionAsync(It.IsAny<CancellationToken>())).ReturnsAsync(reprovisionedKey);
        return keys;
    }

    public static GatewayApiClient CreateClient(
        HttpMessageHandler handler,
        IAgentKeyProvider keys,
        TimeSpan? idleTimeout = null,
        Func<TimeSpan, CancellationToken, Task>? retryDelay = null) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("http://localhost:8080") },
            keys, NullLogger<GatewayApiClient>.Instance,
            idleTimeout, retryDelay ?? ((_, _) => Task.CompletedTask));

    public static GatewayCompletionRequest CompletionRequest() => new()
    {
        Model = "gpt-4o-mini",
        Messages = new List<GatewayMessage> { new() { Role = "user", Content = "hi" } }
    };

    public static async Task<List<SseFrame>> CollectAsync(
        GatewayApiClient client, CancellationToken ct = default)
    {
        var frames = new List<SseFrame>();
        await foreach (var frame in client.StreamCompletionAsync(CompletionRequest(), ct))
            frames.Add(frame);
        return frames;
    }
}
