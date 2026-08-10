using System;
using System.Globalization;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// Compact per-message timestamp text (audit §3.8 — the bubble carried
/// CreatedAt but never showed it). Pure and WPF-free so it is unit-testable;
/// the bubble binds <c>MessageViewModel.TimestampText</c>.
///
/// Timezone: ALWAYS the local machine zone (users are in Asia/Bangkok).
/// CreatedAt is a <see cref="DateTimeOffset"/> on every path — live sends use
/// DateTimeOffset.Now, SQLite round-trips ISO-8601 "o" normalized to UTC, and
/// server DTOs deserialize the offset carried in the JSON — so the value is
/// always instant-correct and <c>ToLocalTime()</c> is the right conversion
/// regardless of which offset it was stored with.
///
/// Format follows the active UI language (LocaleService.Locale): the EN dict
/// renders "6 Aug 14:32", the TH dict "6 ส.ค. 14:32". No new Dict keys are
/// needed — the month name comes from the culture, the layout is identical.
/// </summary>
public static class MessageTimestampFormatter
{
    private const string TodayPattern = "HH:mm";
    private const string OlderPattern = "d MMM HH:mm";

    private static readonly CultureInfo ThaiCulture = CultureInfo.GetCultureInfo("th-TH");

    /// <summary>Time-only for a message sent today, day+month otherwise.</summary>
    public static string Format(DateTimeOffset createdAt, DateTimeOffset now, string? locale)
    {
        var local = createdAt.ToLocalTime();
        var isToday = local.Date == now.ToLocalTime().Date;
        return local.ToString(isToday ? TodayPattern : OlderPattern, CultureFor(locale));
    }

    private static CultureInfo CultureFor(string? locale) =>
        string.Equals(locale, "th", StringComparison.OrdinalIgnoreCase)
            ? ThaiCulture
            : CultureInfo.InvariantCulture;
}
