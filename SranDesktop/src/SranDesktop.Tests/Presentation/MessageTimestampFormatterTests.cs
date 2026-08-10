using System;
using System.Globalization;
using System.Text.RegularExpressions;
using FluentAssertions;
using SranDesktop.Presentation.Services;
using Xunit;

namespace SranDesktop.Tests.Presentation;

/// <summary>Audit §3.8: the message bubble now renders CreatedAt as a compact
/// LOCAL time. The rendering itself is a visual check; the format decision is
/// pure and tested here.</summary>
public class MessageTimestampFormatterTests
{
    /// <summary>Local midday today — a stable "now" that can't drift over midnight.</summary>
    private static DateTimeOffset LocalNoonToday() => new(DateTime.Today.AddHours(12));

    [Fact]
    public void Today_Shows_Time_Only()
    {
        var now = LocalNoonToday();
        var created = now.AddHours(-2);

        var text = MessageTimestampFormatter.Format(created, now, "en");

        text.Should().MatchRegex(@"^\d{2}:\d{2}$");
        text.Should().Be(created.ToString("HH:mm", CultureInfo.InvariantCulture));
    }

    [Fact]
    public void Older_Message_Shows_Day_And_Month_With_Time()
    {
        var now = LocalNoonToday();
        var created = now.AddDays(-3);

        var text = MessageTimestampFormatter.Format(created, now, "en");

        text.Should().Be(created.ToString("d MMM HH:mm", CultureInfo.InvariantCulture));
        text.Should().MatchRegex(@"^\d{1,2} \w+ \d{2}:\d{2}$");
    }

    [Fact]
    public void Yesterday_Is_Treated_As_Older_Not_Today()
    {
        var now = LocalNoonToday();

        var text = MessageTimestampFormatter.Format(now.AddDays(-1), now, "en");

        text.Should().NotMatchRegex(@"^\d{2}:\d{2}$");
    }

    [Fact]
    public void Utc_Input_Renders_In_Local_Machine_Time()
    {
        // 07:32Z — what a server/SQLite round-trip hands us.
        var utc = new DateTimeOffset(2026, 8, 6, 7, 32, 0, TimeSpan.Zero);
        var expectedLocal = utc.ToLocalTime();

        // "now" is days later, so this takes the day+month branch and the whole
        // rendered value (date AND time) is asserted against the local zone.
        var text = MessageTimestampFormatter.Format(utc, utc.AddDays(5), "en");

        text.Should().Be(expectedLocal.ToString("d MMM HH:mm", CultureInfo.InvariantCulture));
    }

    [Fact]
    public void Same_Instant_Renders_Identically_Whatever_Offset_It_Carries()
    {
        // Guards the 7-hour-shift bug class: the SAME instant expressed as UTC
        // and as +07:00 (Asia/Bangkok, how a live send is stamped) must render
        // the same. A naive format of the raw offset value would differ by 7h.
        var asUtc = new DateTimeOffset(2026, 8, 6, 7, 32, 0, TimeSpan.Zero);
        var asBangkok = asUtc.ToOffset(TimeSpan.FromHours(7));

        var now = asUtc;

        MessageTimestampFormatter.Format(asBangkok, now, "en")
            .Should().Be(MessageTimestampFormatter.Format(asUtc, now, "en"));
    }

    [Fact]
    public void Thai_Locale_Localizes_The_Month_Name()
    {
        var now = LocalNoonToday();
        var created = now.AddDays(-3);

        var en = MessageTimestampFormatter.Format(created, now, "en");
        var th = MessageTimestampFormatter.Format(created, now, "th");

        th.Should().NotBe(en);
        // Same clock time in both languages — only the month name is translated.
        th.Should().EndWith(created.ToString("HH:mm", CultureInfo.InvariantCulture));
        Regex.IsMatch(th, @"\p{IsThai}").Should().BeTrue();
    }

    [Fact]
    public void Unknown_Locale_Falls_Back_To_English_Format()
    {
        var now = LocalNoonToday();
        var created = now.AddDays(-3);

        MessageTimestampFormatter.Format(created, now, "fr")
            .Should().Be(MessageTimestampFormatter.Format(created, now, "en"));
        MessageTimestampFormatter.Format(created, now, null)
            .Should().Be(MessageTimestampFormatter.Format(created, now, "en"));
    }
}
