using System.Text;

namespace SranDesktop.Infrastructure.Storage;

/// <summary>
/// Crash-safe file replacement: write to a sibling .tmp, flush to DISK (not
/// just the OS cache), then atomically move over the target. Power loss
/// mid-write leaves either the old file intact or a stale .tmp — never a
/// truncated target. Used for every app-owned state file (settings,
/// credentials, projects, connector state, skill flags).
/// </summary>
public static class AtomicFile
{
    public static void Write(string path, string contents) =>
        WriteBytes(path, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false).GetBytes(contents));

    public static void WriteBytes(string path, byte[] bytes)
    {
        var tmp = path + ".tmp";
        if (File.Exists(tmp))
            File.Delete(tmp); // stale leftover from a previous crash
        using (var stream = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(flushToDisk: true);
        }
        File.Move(tmp, path, overwrite: true);
    }

    /// <summary>Preserves an unreadable file as "{path}.corrupt-{timestamp}"
    /// before the caller falls back to defaults, so the user's data is never
    /// silently discarded. Best-effort; returns the backup path or null.</summary>
    public static string? TryBackupCorrupt(string path)
    {
        try
        {
            if (!File.Exists(path))
                return null;
            var backup = $"{path}.corrupt-{DateTime.Now:yyyyMMdd-HHmmss}";
            File.Copy(path, backup, overwrite: true);
            return backup;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }
}
