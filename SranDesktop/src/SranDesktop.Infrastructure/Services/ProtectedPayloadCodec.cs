using System.Security.Cryptography;
using System.Text;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Current-user DPAPI envelope for durable payloads that must remain usable
/// after restart but must not appear as plaintext in SQLite.
/// </summary>
internal static class ProtectedPayloadCodec
{
    private const string Prefix = "dpapi:v1:";
    private static readonly byte[] Entropy =
        Encoding.UTF8.GetBytes("SRAN.Desktop.DurablePayload.v1");

    public static string Protect(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        if (IsProtected(value))
            return value;
        var plaintext = Encoding.UTF8.GetBytes(value);
        try
        {
            var protectedBytes = ProtectedData.Protect(
                plaintext,
                Entropy,
                DataProtectionScope.CurrentUser);
            return Prefix + Convert.ToBase64String(protectedBytes);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }

    public static string Unprotect(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        if (!IsProtected(value))
            return value;
        var protectedBytes = Convert.FromBase64String(value[Prefix.Length..]);
        var plaintext = ProtectedData.Unprotect(
            protectedBytes,
            Entropy,
            DataProtectionScope.CurrentUser);
        try
        {
            return Encoding.UTF8.GetString(plaintext);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
        }
    }

    public static bool IsProtected(string? value) =>
        value?.StartsWith(Prefix, StringComparison.Ordinal) == true;
}
