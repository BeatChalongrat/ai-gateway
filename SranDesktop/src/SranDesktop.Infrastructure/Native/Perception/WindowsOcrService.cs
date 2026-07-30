using System.Drawing;
using System.Drawing.Imaging;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage.Streams;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Infrastructure.Native.Perception;

public sealed record LocalOcrRegion(
    int Reference,
    string Text,
    int X,
    int Y,
    int Width,
    int Height,
    double GroundingConfidence,
    string ConfidenceBasis);

public sealed record LocalOcrObservation(
    string ObservationId,
    string CaptureId,
    long CaptureVersion,
    string Language,
    DateTimeOffset CapturedAtUtc,
    SensitiveDataClassification Sensitivity,
    IReadOnlyList<LocalOcrRegion> Regions);

public sealed class WindowsOcrService
{
    public async Task<LocalOcrObservation> RecognizeAsync(
        Bitmap bitmap,
        string captureId,
        long captureVersion,
        CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(bitmap);
        var engine = OcrEngine.TryCreateFromUserProfileLanguages();
        if (engine is null)
            throw new InvalidOperationException(
                "LocalOcrUnavailable: Windows has no OCR language available.");

        await using var png = new MemoryStream();
        bitmap.Save(png, ImageFormat.Png);
        var bytes = png.ToArray();
        using var randomAccess = new InMemoryRandomAccessStream();
        using (var writer = new DataWriter(randomAccess))
        {
            writer.WriteBytes(bytes);
            await writer.StoreAsync().AsTask(ct);
            await writer.FlushAsync().AsTask(ct);
            writer.DetachStream();
        }
        randomAccess.Seek(0);
        var decoder = await BitmapDecoder.CreateAsync(randomAccess).AsTask(ct);
        using var softwareBitmap = await decoder.GetSoftwareBitmapAsync(
                BitmapPixelFormat.Bgra8,
                BitmapAlphaMode.Premultiplied)
            .AsTask(ct);
        var result = await engine.RecognizeAsync(softwareBitmap).AsTask(ct);
        var regions = result.Lines
            .Where(line => !string.IsNullOrWhiteSpace(line.Text))
            .Select((line, index) =>
            {
                var words = line.Words.ToArray();
                var left = words.Min(word => word.BoundingRect.X);
                var top = words.Min(word => word.BoundingRect.Y);
                var right = words.Max(word =>
                    word.BoundingRect.X + word.BoundingRect.Width);
                var bottom = words.Max(word =>
                    word.BoundingRect.Y + word.BoundingRect.Height);
                return new LocalOcrRegion(
                    index,
                    line.Text.Trim(),
                    (int)Math.Floor(left),
                    (int)Math.Floor(top),
                    Math.Max(1, (int)Math.Ceiling(right - left)),
                    Math.Max(1, (int)Math.Ceiling(bottom - top)),
                    0.90,
                    "windows_ocr_line:platform_confidence_not_exposed");
            })
            .ToArray();
        var combined = string.Join(Environment.NewLine, regions.Select(item => item.Text));
        return new LocalOcrObservation(
            $"ocr_{Guid.NewGuid():N}",
            captureId,
            captureVersion,
            engine.RecognizerLanguage.LanguageTag,
            DateTimeOffset.UtcNow,
            SensitiveDataRedactor.Classify(combined),
            regions);
    }
}

public sealed record OcrSnapshot(
    LocalOcrObservation Observation,
    CaptureContext Capture,
    DateTimeOffset ExpiresAtUtc);

public sealed class OcrSnapshotStore
{
    private const int MaximumSnapshots = 16;
    private readonly object _gate = new();
    private readonly Dictionary<string, OcrSnapshot> _snapshots =
        new(StringComparer.Ordinal);

    public void Save(OcrSnapshot snapshot)
    {
        lock (_gate)
        {
            _snapshots[snapshot.Observation.ObservationId] = snapshot;
            foreach (var stale in _snapshots.Values
                         .Where(item => item.ExpiresAtUtc <= DateTimeOffset.UtcNow)
                         .Select(item => item.Observation.ObservationId)
                         .ToArray())
                _snapshots.Remove(stale);
            foreach (var oldest in _snapshots.Values
                         .OrderBy(item => item.Observation.CapturedAtUtc)
                         .Take(Math.Max(0, _snapshots.Count - MaximumSnapshots))
                         .Select(item => item.Observation.ObservationId)
                         .ToArray())
                _snapshots.Remove(oldest);
        }
    }

    public bool TryResolve(
        string observationId,
        int reference,
        out OcrSnapshot? snapshot,
        out LocalOcrRegion? region)
    {
        lock (_gate)
        {
            if (!_snapshots.TryGetValue(observationId, out snapshot)
                || snapshot.ExpiresAtUtc <= DateTimeOffset.UtcNow)
            {
                if (snapshot is not null)
                    _snapshots.Remove(observationId);
                snapshot = null;
                region = null;
                return false;
            }
            region = snapshot.Observation.Regions.FirstOrDefault(
                item => item.Reference == reference);
            return region is not null;
        }
    }
}
