using System.Text.RegularExpressions;

namespace SranDesktop.Application.Services;

/// <summary>
/// Hard safety block shared by every synthetic-typing path (computer_type,
/// uia_set_value, browser_type): credentials never flow through agent input.
/// One implementation on purpose — the guard previously lived in
/// ComputerTypeTool and matched only password|passwd|pwd, which let
/// "pass : hunter2" through (a gap found the hard way). The extension's
/// content.js keeps a parallel FIELD-name list (SRAN_CREDENTIAL_FIELDS) —
/// update both together.
/// </summary>
public static class CredentialTextGuard
{
    // Word-boundary label list: matches "pass: x" / "api key = y" but not
    // "passage", "tokenize", "compass", "pinned" (no boundary + no separator).
    private static readonly Regex LabeledSecret = new(
        @"(?i)\b(password|passwd|pwd|pass|secret|token|api[ _-]?key|otp|pin)\b\s*[:=]\s*\S+",
        RegexOptions.Compiled);

    // API-key shapes (vendor prefixes + long opaque tokens).
    private static readonly Regex KeyShape = new(
        @"\b(sk-|gw_|ghp_|github_pat_|xox[bap]-|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,})",
        RegexOptions.Compiled);

    // Credit-card numbers: 13-19 digits allowing spaces/dashes.
    private static readonly Regex CardDigits = new(@"\b(?:\d[ -]?){13,19}\b", RegexOptions.Compiled);
    private static readonly Regex CardShape = new(@"\d{13,}|\d{4}[ -]\d{4}", RegexOptions.Compiled);

    public static bool LooksLikeCredential(string text, out string kind)
    {
        kind = "";
        if (KeyShape.IsMatch(text))
        {
            kind = "API key or access token";
            return true;
        }
        if (CardDigits.IsMatch(text) && CardShape.IsMatch(text))
        {
            kind = "credit card number";
            return true;
        }
        if (LabeledSecret.IsMatch(text))
        {
            kind = "password or secret";
            return true;
        }
        return false;
    }
}
