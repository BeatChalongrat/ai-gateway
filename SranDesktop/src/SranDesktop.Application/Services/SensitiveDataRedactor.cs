using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public static partial class SensitiveDataRedactor
{
    public static string Redact(string? value)
    {
        if (string.IsNullOrEmpty(value)) return value ?? string.Empty;
        var redacted = JsonSecretPropertyRegex().Replace(value, "$1[REDACTED]$3");
        redacted = KeyValueSecretRegex().Replace(redacted, "$1[REDACTED]");
        redacted = AuthorizationHeaderRegex().Replace(redacted, "Bearer [REDACTED_TOKEN]");
        redacted = PrivateKeyRegex().Replace(redacted, "[REDACTED_PRIVATE_KEY]");
        redacted = CredentialUriRegex().Replace(redacted, "$1[REDACTED]@");
        redacted = OpaqueTokenRegex().Replace(redacted, "[REDACTED_TOKEN]");
        redacted = CloudAccessKeyRegex().Replace(redacted, "[REDACTED_CLOUD_KEY]");
        redacted = EmailRegex().Replace(redacted, "[REDACTED_EMAIL]");
        redacted = ThaiPersonalIdRegex().Replace(
            redacted,
            match => IsValidThaiPersonalId(match.Value)
                ? "[REDACTED_THAI_ID]"
                : match.Value);
        return PhoneRegex().Replace(redacted, "[REDACTED_PHONE]");
    }

    public static SensitiveDataClassification Classify(string? value)
    {
        if (string.IsNullOrEmpty(value)) return SensitiveDataClassification.Internal;
        if (JsonSecretPropertyRegex().IsMatch(value)
            || KeyValueSecretRegex().IsMatch(value)
            || AuthorizationHeaderRegex().IsMatch(value)
            || PrivateKeyRegex().IsMatch(value)
            || CredentialUriRegex().IsMatch(value)
            || OpaqueTokenRegex().IsMatch(value)
            || CloudAccessKeyRegex().IsMatch(value))
            return SensitiveDataClassification.Secret;
        if (EmailRegex().IsMatch(value)
            || ThaiPersonalIdRegex().Matches(value)
                .Any(match => IsValidThaiPersonalId(match.Value))
            || PhoneRegex().IsMatch(value))
            return SensitiveDataClassification.Personal;
        return SensitiveDataClassification.Internal;
    }

    public static string RedactJson(string? value)
    {
        if (string.IsNullOrEmpty(value))
            return value ?? string.Empty;
        try
        {
            using var document = JsonDocument.Parse(value);
            using var stream = new MemoryStream();
            using (var writer = new Utf8JsonWriter(stream))
                WriteRedacted(writer, document.RootElement);
            return Encoding.UTF8.GetString(stream.ToArray());
        }
        catch (JsonException)
        {
            return Redact(value);
        }
    }

    public static string Fingerprint(string? value)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(value ?? string.Empty));
        return Convert.ToHexString(bytes);
    }

    public static string SummarizeJsonForPersistence(string? value)
    {
        var byteCount = Encoding.UTF8.GetByteCount(value ?? string.Empty);
        return JsonSerializer.Serialize(new
        {
            persisted_payload_removed = true,
            byte_count = byteCount
        });
    }

    public static string SummarizeTextForPersistence(string kind, string? value)
    {
        var byteCount = Encoding.UTF8.GetByteCount(value ?? string.Empty);
        return $"[{kind} removed before persistence; byte_count={byteCount}]";
    }

    private static bool IsValidThaiPersonalId(string value)
    {
        Span<int> digits = stackalloc int[13];
        var count = 0;
        foreach (var character in value)
        {
            if (character == '-')
                continue;
            if (character is < '0' or > '9' || count >= digits.Length)
                return false;
            digits[count++] = character - '0';
        }
        if (count != digits.Length)
            return false;

        var sum = 0;
        for (var index = 0; index < 12; index++)
            sum += digits[index] * (13 - index);
        return (11 - sum % 11) % 10 == digits[12];
    }

    private static void WriteRedacted(Utf8JsonWriter writer, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (var property in element.EnumerateObject())
                {
                    writer.WritePropertyName(property.Name);
                    if (SensitivePropertyNameRegex().IsMatch(property.Name))
                        writer.WriteStringValue("[REDACTED]");
                    else
                        WriteRedacted(writer, property.Value);
                }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in element.EnumerateArray())
                    WriteRedacted(writer, item);
                writer.WriteEndArray();
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(Redact(element.GetString()));
                break;
            case JsonValueKind.Number:
                writer.WriteRawValue(element.GetRawText());
                break;
            case JsonValueKind.True:
                writer.WriteBooleanValue(true);
                break;
            case JsonValueKind.False:
                writer.WriteBooleanValue(false);
                break;
            default:
                writer.WriteNullValue();
                break;
        }
    }

    [GeneratedRegex(
        @"(?i)^(?:password|passwd|pwd|secret|token|access_token|refresh_token|api[_-]?key|authorization|client_secret|connection_string)$")]
    private static partial Regex SensitivePropertyNameRegex();

    [GeneratedRegex(
        @"(?i)(""(?:password|passwd|pwd|secret|token|access_token|refresh_token|api[_-]?key|authorization|client_secret|connection_string)""\s*:\s*"")((?:\\.|[^""\\])*)("")")]
    private static partial Regex JsonSecretPropertyRegex();

    [GeneratedRegex(
        @"(?i)((?:password|passwd|pwd|secret|token|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|authorization|client[_ -]?secret|connection[_ -]?string|aws_secret_access_key)\s*[:=]\s*)(?:""[^""]*""|'[^']*'|[^\s,;]+)")]
    private static partial Regex KeyValueSecretRegex();

    [GeneratedRegex(@"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")]
    private static partial Regex AuthorizationHeaderRegex();

    [GeneratedRegex(
        @"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
    private static partial Regex PrivateKeyRegex();

    [GeneratedRegex(@"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@/\s]+@")]
    private static partial Regex CredentialUriRegex();

    [GeneratedRegex(
        @"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{16,}|1//[A-Za-z0-9_-]{16,})\b")]
    private static partial Regex OpaqueTokenRegex();

    [GeneratedRegex(@"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")]
    private static partial Regex CloudAccessKeyRegex();

    [GeneratedRegex(@"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")]
    private static partial Regex EmailRegex();

    [GeneratedRegex(@"(?<![\w])(?:\d-\d{4}-\d{5}-\d{2}-\d|\d{13})(?![\w])")]
    private static partial Regex ThaiPersonalIdRegex();

    [GeneratedRegex(@"(?<![\w])(?:\+66|0)\s?\d{1,2}[-\s]?\d{3}[-\s]?\d{4}(?![\w])")]
    private static partial Regex PhoneRegex();
}
