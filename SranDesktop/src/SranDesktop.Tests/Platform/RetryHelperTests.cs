using System.Net;
using System.Net.Http;
using FluentAssertions;
using SranDesktop.Infrastructure.Api;
using Xunit;

namespace SranDesktop.Tests.Platform;

public class RetryHelperTests
{
    [Fact]
    public async Task Succeeds_First_Try_No_Delay()
    {
        var calls = 0;
        var result = await RetryHelper.WithRetryAsync(() =>
        {
            calls++;
            return Task.FromResult(42);
        }, baseDelayMs: 1);

        result.Should().Be(42);
        calls.Should().Be(1);
    }

    [Fact]
    public async Task Retries_Transient_5xx_Then_Succeeds()
    {
        var calls = 0;
        var result = await RetryHelper.WithRetryAsync(() =>
        {
            calls++;
            if (calls < 3)
                throw new HttpRequestException("boom", null, HttpStatusCode.BadGateway);
            return Task.FromResult("ok");
        }, baseDelayMs: 1);

        result.Should().Be("ok");
        calls.Should().Be(3);
    }

    [Fact]
    public async Task Retries_Connection_Error_With_Null_Status()
    {
        var calls = 0;
        var result = await RetryHelper.WithRetryAsync(() =>
        {
            calls++;
            if (calls < 2)
                throw new HttpRequestException("connection refused"); // StatusCode null
            return Task.FromResult(1);
        }, baseDelayMs: 1);

        result.Should().Be(1);
        calls.Should().Be(2);
    }

    [Fact]
    public async Task Does_Not_Retry_Non_Transient_4xx()
    {
        var calls = 0;
        var act = () => RetryHelper.WithRetryAsync<int>(() =>
        {
            calls++;
            throw new HttpRequestException("unauthorized", null, HttpStatusCode.Unauthorized);
        }, baseDelayMs: 1);

        await act.Should().ThrowAsync<HttpRequestException>();
        calls.Should().Be(1);
    }

    [Fact]
    public async Task Gives_Up_After_MaxRetries()
    {
        var calls = 0;
        var act = () => RetryHelper.WithRetryAsync<int>(() =>
        {
            calls++;
            throw new HttpRequestException("always down", null, HttpStatusCode.ServiceUnavailable);
        }, maxRetries: 2, baseDelayMs: 1);

        await act.Should().ThrowAsync<HttpRequestException>();
        calls.Should().Be(3); // initial + 2 retries
    }
}
