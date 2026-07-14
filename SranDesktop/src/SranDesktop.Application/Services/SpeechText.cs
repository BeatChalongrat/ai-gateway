namespace SranDesktop.Application.Services;

/// <summary>Text prep for TTS (moved out of the old VoiceService in R10 Fix 2).</summary>
public static class SpeechText
{
    /// <summary>Rough markdown → plain text so TTS doesn't read symbols aloud.</summary>
    public static string StripMarkdown(string text)
    {
        var sb = new System.Text.StringBuilder(text.Length);
        var inCodeFence = false;
        foreach (var rawLine in text.Split('\n'))
        {
            var line = rawLine;
            if (line.TrimStart().StartsWith("```", StringComparison.Ordinal))
            {
                inCodeFence = !inCodeFence;
                continue;
            }
            if (inCodeFence)
                continue;
            line = line.Replace("**", "").Replace("__", "").Replace("`", "");
            line = System.Text.RegularExpressions.Regex.Replace(line, @"^#{1,6}\s*", "");
            line = System.Text.RegularExpressions.Regex.Replace(line, @"\[([^\]]+)\]\([^)]*\)", "$1");
            sb.AppendLine(line);
        }
        return sb.ToString();
    }
}
