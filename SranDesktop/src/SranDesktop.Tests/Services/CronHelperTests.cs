using System;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

// Re-homed from the retired R9/Round9FeatureTests when the WPF projects were
// removed. CronHelper lives in SranDesktop.Application (kept) and is called by
// the shipping SranDesktop.ToolsSidecar (HostQuerySurface: task create/update/
// setEnabled/reschedule/catch-up all resolve the next run through it, and the
// bad-cron refusal depends on NextOccurrence returning null). This pins that
// contract so it does not go untested once R9 is gone.
public class CronHelperTests
{
    [Theory]
    [InlineData("0 6 * * *")]
    [InlineData("*/5 * * * *")]
    [InlineData("0 6 * * 1")]
    public void Valid_Cron_Yields_Future_Occurrence(string cron)
    {
        var next = CronHelper.NextOccurrence(cron);
        Assert.NotNull(next);
        Assert.True(next > DateTimeOffset.UtcNow.AddSeconds(-1));
    }

    [Theory]
    [InlineData("not a cron")]
    [InlineData("99 99 * * *")]
    [InlineData("")]
    public void Invalid_Cron_Returns_Null(string cron)
    {
        Assert.Null(CronHelper.NextOccurrence(cron));
    }
}
