using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Win32;

namespace SranDesktop.Infrastructure.Native.Browser;

/// <summary>
/// Deterministic validation shared by the installer and the dependency-free
/// native relay. Chrome's manifest allowlist is checked again by the relay.
/// </summary>
public static class BrowserNativeHostPolicy
{
    public const string HostName = "com.sran.browser";
    public const string TestHostName = "com.sran.browser.e2e";
    public const string RegistryKeyPath =
        @"Software\Google\Chrome\NativeMessagingHosts\" + HostName;
    public static string EffectiveHostName =>
        IsTestMode ? TestHostName : HostName;
    public static string EffectiveRegistryKeyPath =>
        @"Software\Google\Chrome\NativeMessagingHosts\" + EffectiveHostName;

    public static bool IsValidExtensionId(string value) =>
        value.Length == 32
        && value.All(character => character is >= 'a' and <= 'p');

    public static string OriginFor(string extensionId)
    {
        if (!IsValidExtensionId(extensionId))
            throw new ArgumentException(
                "Chrome extension IDs must contain exactly 32 characters from a-p.",
                nameof(extensionId));
        return $"chrome-extension://{extensionId}/";
    }

    public static bool IsAllowedInvocation(
        IReadOnlyList<string> arguments,
        string? manifestPath = null)
    {
        var origin = arguments.FirstOrDefault(argument =>
            argument.StartsWith("chrome-extension://", StringComparison.Ordinal));
        if (origin is null)
            return false;
        origin = origin.EndsWith("/", StringComparison.Ordinal)
            ? origin
            : origin + "/";

        manifestPath ??= RegisteredManifestPath();
        if (string.IsNullOrWhiteSpace(manifestPath)
            || !TryReadManifest(manifestPath, out var manifest))
            return false;
        return manifest.AllowedOrigins.Contains(origin, StringComparer.Ordinal);
    }

    public static bool TryReadManifest(
        string manifestPath,
        out BrowserNativeHostManifest manifest)
    {
        manifest = null!;
        try
        {
            var fullManifest = Path.GetFullPath(manifestPath);
            if (!File.Exists(fullManifest)
                || (File.GetAttributes(fullManifest) & FileAttributes.ReparsePoint) != 0)
                return false;
            using var document = JsonDocument.Parse(File.ReadAllText(fullManifest));
            var root = document.RootElement;
            var name = root.GetProperty("name").GetString() ?? "";
            var type = root.GetProperty("type").GetString() ?? "";
            var executable = Path.GetFullPath(root.GetProperty("path").GetString() ?? "");
            var origins = root.GetProperty("allowed_origins")
                .EnumerateArray()
                .Select(item => item.GetString() ?? "")
                .Where(origin => origin.StartsWith(
                    "chrome-extension://",
                    StringComparison.Ordinal))
                .Distinct(StringComparer.Ordinal)
                .ToArray();
            if (!string.Equals(name, EffectiveHostName, StringComparison.Ordinal)
                || !string.Equals(type, "stdio", StringComparison.Ordinal)
                || !IsSafeExecutable(executable)
                || origins.Length == 0)
                return false;

            var expectedHash = root.TryGetProperty(
                    "sran_executable_sha256",
                    out var hash)
                ? hash.GetString()
                : null;
            if (!string.IsNullOrWhiteSpace(expectedHash)
                && !CryptographicOperations.FixedTimeEquals(
                    Convert.FromHexString(expectedHash),
                    SHA256.HashData(File.ReadAllBytes(executable))))
                return false;

            manifest = new BrowserNativeHostManifest(
                fullManifest,
                executable,
                origins,
                expectedHash);
            return true;
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException
                or JsonException or InvalidOperationException
                or ArgumentException or FormatException)
        {
            return false;
        }
    }

    public static bool IsSafeExecutable(string path)
    {
        try
        {
            var full = Path.GetFullPath(path);
            return string.Equals(
                       Path.GetExtension(full),
                       ".exe",
                       StringComparison.OrdinalIgnoreCase)
                   && File.Exists(full)
                   && (File.GetAttributes(full) & FileAttributes.ReparsePoint) == 0;
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException
                or ArgumentException or NotSupportedException)
        {
            return false;
        }
    }

    public static string Sha256(string path) =>
        Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path)))
            .ToLowerInvariant();

    private static string? RegisteredManifestPath()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(EffectiveRegistryKeyPath);
            return key?.GetValue(null) as string;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static bool IsTestMode => string.Equals(
        Environment.GetEnvironmentVariable("SRAN_BROWSER_TEST_MODE"),
        "1",
        StringComparison.Ordinal);
}

public sealed record BrowserNativeHostManifest(
    string ManifestPath,
    string ExecutablePath,
    IReadOnlyList<string> AllowedOrigins,
    string? ExecutableSha256);
