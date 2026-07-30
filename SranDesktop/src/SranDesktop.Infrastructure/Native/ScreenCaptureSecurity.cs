using System.Drawing;

namespace SranDesktop.Infrastructure.Native;

public sealed class ScreenCaptureSecurityOptions
{
    public bool DenyUnknownExternalClassification { get; init; } = true;

    public IReadOnlyList<string> DeniedProcessNames { get; init; } =
    [
        "CredentialUIBroker",
        "KeePass",
        "KeePassXC",
        "1Password",
        "Bitwarden",
        "CredentialUI"
    ];

    public IReadOnlyList<string> DeniedWindowTitleFragments { get; init; } =
    [
        "Windows Security",
        "Credential Manager",
        "Enter your credentials",
        "Security key"
    ];

    public IReadOnlyList<ScreenRedactionRule> RedactionRules { get; init; } = [];
}

public sealed record ScreenRedactionRule(
    ImageRect Region,
    string? ProcessName = null,
    string? WindowTitleFragment = null,
    CaptureType? CaptureType = null,
    string? MonitorId = null);

public sealed record ScreenCaptureSecurityContext(
    CaptureType CaptureType,
    string? ProcessName,
    string? WindowTitle,
    string? MonitorId,
    int ImageWidth,
    int ImageHeight);

public sealed record ScreenCapturePolicyDecision(
    bool Allowed,
    bool ClassificationKnown,
    IReadOnlyList<ImageRect> RedactionRegions,
    string DecisionCode,
    string Reason);

public interface IScreenCaptureSecurityPolicy
{
    ScreenCapturePolicyDecision Evaluate(ScreenCaptureSecurityContext context);
}

public interface IScreenImageRedactor
{
    void Redact(Bitmap image, IReadOnlyList<ImageRect> regions);
}

public interface IScreenCaptureIndicator
{
    event Action<bool, string?>? CaptureStateChanged;
    IDisposable Begin(string scope);
}

/// <summary>
/// Deterministic screen policy. Model-produced content cannot change the
/// denylist, downgrade unknown classification, or remove redaction regions.
/// </summary>
public sealed class ScreenCaptureSecurityPolicy : IScreenCaptureSecurityPolicy
{
    private readonly ScreenCaptureSecurityOptions _options;

    public ScreenCaptureSecurityPolicy(ScreenCaptureSecurityOptions? options = null) =>
        _options = options ?? new ScreenCaptureSecurityOptions();

    public ScreenCapturePolicyDecision Evaluate(ScreenCaptureSecurityContext context)
    {
        ArgumentNullException.ThrowIfNull(context);
        var process = NormalizeProcessName(context.ProcessName);
        if (process is not null
            && _options.DeniedProcessNames.Any(item =>
                string.Equals(
                    NormalizeProcessName(item),
                    process,
                    StringComparison.OrdinalIgnoreCase)))
        {
            return Deny("ProtectedApplication", "The target application is protected by screen policy.");
        }

        if (context.WindowTitle is { Length: > 0 } title
            && _options.DeniedWindowTitleFragments.Any(fragment =>
                title.Contains(fragment, StringComparison.OrdinalIgnoreCase)))
        {
            return Deny("ProtectedWindow", "The target window is protected by screen policy.");
        }

        var classificationKnown = context.CaptureType == CaptureType.Window
                                  && process is { Length: > 0 }
                                  && context.WindowTitle is { Length: > 0 };
        if (!classificationKnown && _options.DenyUnknownExternalClassification)
        {
            return Deny(
                "UnknownScreenClassification",
                "Screen classification is unknown and policy requires fail-closed protection.");
        }

        var regions = _options.RedactionRules
            .Where(rule => Matches(rule, context, process))
            .Select(rule => Clamp(rule.Region, context.ImageWidth, context.ImageHeight))
            .Where(region => region is not null)
            .Cast<ImageRect>()
            .ToArray();
        return new ScreenCapturePolicyDecision(
            true,
            classificationKnown,
            regions,
            regions.Length == 0 ? "Allowed" : "AllowedWithRedaction",
            regions.Length == 0
                ? "The capture target passed application and window policy."
                : $"{regions.Length} configured sensitive region(s) require redaction.");
    }

    private static bool Matches(
        ScreenRedactionRule rule,
        ScreenCaptureSecurityContext context,
        string? normalizedProcess)
    {
        if (rule.CaptureType is { } captureType && captureType != context.CaptureType)
            return false;
        if (rule.ProcessName is { Length: > 0 }
            && !string.Equals(
                NormalizeProcessName(rule.ProcessName),
                normalizedProcess,
                StringComparison.OrdinalIgnoreCase))
            return false;
        if (rule.WindowTitleFragment is { Length: > 0 } title
            && !(context.WindowTitle?.Contains(title, StringComparison.OrdinalIgnoreCase) ?? false))
            return false;
        return rule.MonitorId is not { Length: > 0 } monitor
               || string.Equals(monitor, context.MonitorId, StringComparison.OrdinalIgnoreCase);
    }

    private static ImageRect? Clamp(ImageRect region, int width, int height)
    {
        var left = Math.Clamp(region.X, 0, width);
        var top = Math.Clamp(region.Y, 0, height);
        var right = Math.Clamp(region.X + region.Width, 0, width);
        var bottom = Math.Clamp(region.Y + region.Height, 0, height);
        return right <= left || bottom <= top
            ? null
            : new ImageRect(left, top, right - left, bottom - top);
    }

    private static string? NormalizeProcessName(string? value) =>
        string.IsNullOrWhiteSpace(value)
            ? null
            : Path.GetFileNameWithoutExtension(value.Trim());

    private static ScreenCapturePolicyDecision Deny(string code, string reason) =>
        new(false, false, [], code, reason);
}

public sealed class SolidScreenImageRedactor : IScreenImageRedactor
{
    public void Redact(Bitmap image, IReadOnlyList<ImageRect> regions)
    {
        ArgumentNullException.ThrowIfNull(image);
        ArgumentNullException.ThrowIfNull(regions);
        using var graphics = Graphics.FromImage(image);
        using var brush = new SolidBrush(Color.Black);
        foreach (var region in regions)
        {
            if (region.Width <= 0 || region.Height <= 0)
                throw new InvalidOperationException("A screen redaction region is invalid.");
            graphics.FillRectangle(brush, region.X, region.Y, region.Width, region.Height);
        }
    }
}

public sealed class ScreenCaptureIndicator : IScreenCaptureIndicator
{
    public event Action<bool, string?>? CaptureStateChanged;

    public IDisposable Begin(string scope)
    {
        CaptureStateChanged?.Invoke(true, scope);
        return new Scope(this);
    }

    private sealed class Scope(ScreenCaptureIndicator owner) : IDisposable
    {
        private ScreenCaptureIndicator? _owner = owner;

        public void Dispose()
        {
            var owner = Interlocked.Exchange(ref _owner, null);
            owner?.CaptureStateChanged?.Invoke(false, null);
        }
    }
}
