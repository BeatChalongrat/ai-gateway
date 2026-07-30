using System.Drawing;
using System.Drawing.Drawing2D;
using System.Security.Cryptography;

namespace SranDesktop.Infrastructure.Native.Perception;

public sealed record VisualElementReference(
    string CaptureId,
    long CaptureVersion,
    ImageRect BoundingBox,
    string VisualFingerprint,
    DateTimeOffset CreatedAtUtc,
    DateTimeOffset ExpiresAtUtc,
    double MinimumSimilarity);

/// <summary>
/// Retains a bounded, short-lived copy of the exact redacted image sent to the
/// model. It is never serialized. A visual action must match a fresh window
/// capture before native input can be dispatched.
/// </summary>
public sealed class VisualSnapshotStore : IDisposable
{
    private const int MaximumSnapshots = 8;
    private readonly object _gate = new();
    private readonly Dictionary<string, Snapshot> _snapshots =
        new(StringComparer.Ordinal);

    public void Save(CaptureContext capture, Bitmap redactedImage)
    {
        ArgumentNullException.ThrowIfNull(capture);
        ArgumentNullException.ThrowIfNull(redactedImage);
        lock (_gate)
        {
            RemoveExpired();
            if (_snapshots.Remove(capture.CaptureId, out var previous))
                previous.Dispose();
            _snapshots[capture.CaptureId] = new Snapshot(
                capture,
                new Bitmap(redactedImage),
                DateTimeOffset.UtcNow.AddSeconds(10));
            foreach (var key in _snapshots.Values
                         .OrderBy(item => item.Capture.CapturedAtUtc)
                         .Take(Math.Max(0, _snapshots.Count - MaximumSnapshots))
                         .Select(item => item.Capture.CaptureId)
                         .ToArray())
            {
                if (_snapshots.Remove(key, out var old))
                    old.Dispose();
            }
        }
    }

    public bool TryValidateLive(
        CaptureContext capture,
        ImageRect region,
        double minimumSimilarity,
        out VisualElementReference? reference,
        out double similarity,
        out string? error)
    {
        reference = null;
        similarity = 0;
        error = null;
        CaptureContext storedCapture;
        DateTimeOffset expiresAtUtc;
        Bitmap baseline;
        lock (_gate)
        {
            RemoveExpired();
            if (!_snapshots.TryGetValue(capture.CaptureId, out var snapshot))
            {
                error = "VisualSnapshotExpired: take a fresh screenshot.";
                return false;
            }
            if (!Equals(snapshot.Capture, capture))
            {
                error = "VisualCaptureIdentityMismatch.";
                return false;
            }
            storedCapture = snapshot.Capture;
            expiresAtUtc = snapshot.ExpiresAtUtc;
            baseline = new Bitmap(snapshot.Image);
        }

        using (baseline)
        {
            if (!IsValidRegion(region, baseline.Width, baseline.Height))
            {
                error = "VisualRegionInvalid.";
                return false;
            }
            if (capture.CaptureType != CaptureType.Window
                || capture.WindowHandle is null
                || !Win32Input.IsWindow(new IntPtr(capture.WindowHandle.Value)))
            {
                error = "VisualLiveValidationRequiresWindow.";
                return false;
            }

            using var freshPhysical = new Bitmap(
                capture.SourceRectPhysical.Width,
                capture.SourceRectPhysical.Height);
            using (var graphics = Graphics.FromImage(freshPhysical))
            {
                var hdc = graphics.GetHdc();
                try
                {
                    if (!Win32Input.PrintWindow(
                            new IntPtr(capture.WindowHandle.Value),
                            hdc,
                            2))
                    {
                        error = "VisualLiveCaptureFailed.";
                        return false;
                    }
                }
                finally
                {
                    graphics.ReleaseHdc(hdc);
                }
            }

            using var fresh = new Bitmap(capture.ImageWidth, capture.ImageHeight);
            using (var graphics = Graphics.FromImage(fresh))
            {
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                graphics.DrawImage(
                    freshPhysical,
                    new Rectangle(0, 0, fresh.Width, fresh.Height));
            }

            var originalSignature = Signature(baseline, region);
            var freshSignature = Signature(fresh, region);
            similarity = Similarity(originalSignature, freshSignature);
            if (similarity < minimumSimilarity)
            {
                error =
                    $"StaleVisualReference: live similarity {similarity:0.000} is below " +
                    $"{minimumSimilarity:0.000}.";
                return false;
            }

            reference = new VisualElementReference(
                capture.CaptureId,
                capture.CapturedAtUtc.ToUnixTimeMilliseconds(),
                region,
                Convert.ToHexString(SHA256.HashData(originalSignature))
                    .ToLowerInvariant(),
                storedCapture.CapturedAtUtc,
                expiresAtUtc,
                minimumSimilarity);
            return true;
        }
    }

    private static byte[] Signature(Bitmap image, ImageRect region)
    {
        using var sample = new Bitmap(8, 8);
        using (var graphics = Graphics.FromImage(sample))
        {
            graphics.InterpolationMode = InterpolationMode.HighQualityBilinear;
            graphics.DrawImage(
                image,
                new Rectangle(0, 0, 8, 8),
                new Rectangle(region.X, region.Y, region.Width, region.Height),
                GraphicsUnit.Pixel);
        }
        var signature = new byte[8 * 8 * 3];
        var offset = 0;
        for (var y = 0; y < 8; y++)
        for (var x = 0; x < 8; x++)
        {
            var color = sample.GetPixel(x, y);
            signature[offset++] = color.R;
            signature[offset++] = color.G;
            signature[offset++] = color.B;
        }
        return signature;
    }

    private static double Similarity(byte[] left, byte[] right)
    {
        long difference = 0;
        for (var i = 0; i < left.Length; i++)
            difference += Math.Abs(left[i] - right[i]);
        return 1 - difference / (left.Length * 255d);
    }

    private static bool IsValidRegion(ImageRect region, int width, int height) =>
        region.Width > 0
        && region.Height > 0
        && region.X >= 0
        && region.Y >= 0
        && region.X + region.Width <= width
        && region.Y + region.Height <= height;

    private void RemoveExpired()
    {
        foreach (var key in _snapshots.Values
                     .Where(item => item.ExpiresAtUtc <= DateTimeOffset.UtcNow)
                     .Select(item => item.Capture.CaptureId)
                     .ToArray())
        {
            if (_snapshots.Remove(key, out var old))
                old.Dispose();
        }
    }

    public void Dispose()
    {
        lock (_gate)
        {
            foreach (var snapshot in _snapshots.Values)
                snapshot.Dispose();
            _snapshots.Clear();
        }
    }

    private sealed record Snapshot(
        CaptureContext Capture,
        Bitmap Image,
        DateTimeOffset ExpiresAtUtc) : IDisposable
    {
        public void Dispose() => Image.Dispose();
    }
}
