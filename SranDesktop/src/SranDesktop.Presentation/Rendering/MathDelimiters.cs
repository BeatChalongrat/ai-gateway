using System.Text;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Batch 2 T1 — math delimiter normalization (desktop port of the web
/// implementation, apps/web-chat/src/lib/mathDelimiters.ts — keep the two
/// in lockstep; the SAME test corpus runs against both and must agree
/// byte-for-byte).
///
/// GPT-family models emit LaTeX-style \[…\] (display) and \(…\) (inline)
/// by default; Markdig's math extension only parses $-delimiters, so those
/// formulas rendered as literal backslash-bracket text. This converts the
/// backslash forms to the $-forms — a standalone whole-line \[…\] becomes a
/// real $$ block (MathBlock → KaTeX after the stream ends, per MathGuard),
/// a mid-paragraph one becomes inline $$…$$, and \(…\) becomes $…$ (which
/// routes to the SAME MathTex inline path $-math uses today; real inline
/// typesetting stays out of scope — see MathBlockControl's Phase 12 C
/// note on why WebView2-per-formula was rejected).
///
/// Safety rules (each pinned by MathDelimiterTests):
///  - fenced code and $$-math regions are protected using the same
///    line-state rules the web block splitter uses (stepFenceState);
///  - inline code spans (any backtick run length) are copied verbatim; an
///    UNCLOSED span protects everything after it (streaming);
///  - an unterminated \[ or \( stays literal until its closer arrives —
///    mirroring how StreamingFenceGuard/MathGuard treat "still streaming";
///  - \(…\) must close on the SAME line;
///  - \]( is an escaped-bracket markdown-link lookalike — left alone;
///  - \\[ is a literal backslash + bracket (escaped pairs consumed whole);
///  - $ and $$ forms are never touched.
/// </summary>
public static class MathDelimiters
{
    public static string Normalize(string src)
    {
        // Fast path — most messages carry no backslash delimiters.
        if (!src.Contains("\\[") && !src.Contains("\\("))
            return src;

        var inFence = false;
        var inMath = false;
        var outLines = new List<string>();
        var run = new List<string>();

        void FlushRun()
        {
            if (run.Count == 0) return;
            outLines.Add(ConvertRun(string.Join("\n", run)));
            run.Clear();
        }

        foreach (var line in src.Split('\n'))
        {
            var wasInRegion = inFence || inMath;
            var isDelimiter = StepFenceState(ref inFence, ref inMath, line);
            var inRegion = inFence || inMath;
            if (isDelimiter || wasInRegion || inRegion)
            {
                FlushRun();
                outLines.Add(line);
            }
            else
            {
                run.Add(line);
            }
        }
        FlushRun();
        return string.Join("\n", outLines);
    }

    /// <summary>Same line-state rules as the web splitter's stepFenceState:
    /// a $$ line inside a fence is code, a fence marker inside a math block
    /// is math — each state only toggles while the other is off.</summary>
    private static bool StepFenceState(ref bool inFence, ref bool inMath, string line)
    {
        var trimmed = line.Trim();
        if (!inMath && (trimmed.StartsWith("```") || trimmed.StartsWith("~~~")))
        {
            inFence = !inFence;
            return true;
        }
        if (!inFence && trimmed.StartsWith("$$"))
        {
            inMath = !inMath;
            return true;
        }
        return false;
    }

    private static string ConvertRun(string text)
    {
        var sb = new StringBuilder(text.Length);
        var i = 0;
        while (i < text.Length)
        {
            var ch = text[i];

            // Inline code span — copy verbatim through the matching close
            // run (same backtick count). Unclosed span: copy the remainder
            // verbatim (streaming protection).
            if (ch == '`')
            {
                var runLen = 0;
                while (i + runLen < text.Length && text[i + runLen] == '`') runLen++;
                var close = FindBacktickClose(text, i + runLen, runLen);
                if (close == -1)
                {
                    sb.Append(text, i, text.Length - i);
                    break;
                }
                sb.Append(text, i, close + runLen - i);
                i = close + runLen;
                continue;
            }

            // Markdown link / image label — copy the whole construct
            // verbatim. Inside a label, \[ and \] are markdown ESCAPES (a
            // literal bracket), not display-math delimiters, so the label is
            // a skip region exactly like an inline code span. An unclosed
            // label falls through to normal scanning, preserving the
            // streaming behaviour.
            if (ch == '[')
            {
                var past = LinkConstructEnd(text, i);
                if (past != -1)
                {
                    sb.Append(text, i, past - i);
                    i = past;
                    continue;
                }
            }

            if (ch == '\\' && i + 1 < text.Length)
            {
                var next = text[i + 1];
                // Escaped backslash — consume the pair so \\[ never reads as \[.
                if (next == '\\')
                {
                    sb.Append("\\\\");
                    i += 2;
                    continue;
                }
                if (next == '[')
                {
                    var close = FindCloser(text, i + 2, ']', sameLine: false);
                    // \]( is an escaped-bracket link lookalike, not math.
                    if (close != -1 && (close + 2 >= text.Length || text[close + 2] != '('))
                    {
                        var inner = text.Substring(i + 2, close - (i + 2));
                        if (IsAtLineStart(text, i) && IsAtLineEnd(text, close + 2))
                            sb.Append("$$\n").Append(TrimNewlines(inner)).Append("\n$$");
                        else
                            sb.Append("$$").Append(inner).Append("$$");
                        i = close + 2;
                        continue;
                    }
                }
                if (next == '(')
                {
                    var close = FindCloser(text, i + 2, ')', sameLine: true);
                    if (close != -1)
                    {
                        sb.Append('$').Append(text, i + 2, close - (i + 2)).Append('$');
                        i = close + 2;
                        continue;
                    }
                }
            }

            sb.Append(ch);
            i++;
        }
        return sb.ToString();
    }

    /// <summary>
    /// If <paramref name="start"/> opens a markdown link or image label that
    /// is actually part of a link — [label](url) or [label][ref] — returns
    /// the index just past the whole construct; -1 for a bare [text] that is
    /// not a link, and for an unclosed label. Bracket depth ignores escaped
    /// brackets, so [a \[b\] c](url) closes on the real ']'.
    /// Twin of linkConstructEnd in apps/web-chat/src/lib/mathDelimiters.ts.
    /// </summary>
    private static int LinkConstructEnd(string text, int start)
    {
        var depth = 1;
        var j = start + 1;
        var labelEnd = -1;
        while (j < text.Length)
        {
            var c = text[j];
            if (c == '\\')
            {
                j += 2; // escaped char (incl. \[ and \]) — never affects depth
                continue;
            }
            if (c == '\n') break; // a label spanning lines is not worth guessing at
            if (c == '[') depth++;
            else if (c == ']')
            {
                depth--;
                if (depth == 0)
                {
                    labelEnd = j;
                    break;
                }
            }
            j++;
        }
        if (labelEnd == -1) return -1;

        if (labelEnd + 1 >= text.Length) return -1;
        var after = text[labelEnd + 1];
        var closer = after switch { '(' => ')', '[' => ']', _ => '\0' };
        if (closer == '\0') return -1; // [text] alone — not a link

        for (var k = labelEnd + 2; k < text.Length; k++)
        {
            var c = text[k];
            if (c == '\\')
            {
                k++;
                continue;
            }
            if (c == '\n') return -1; // unterminated target
            if (c == closer) return k + 1;
        }
        return -1;
    }

    private static int FindCloser(string text, int from, char closerChar, bool sameLine)
    {
        for (var j = from; j < text.Length - 1; j++)
        {
            var c = text[j];
            if (sameLine && c == '\n') return -1;
            if (c == '\\')
            {
                if (text[j + 1] == '\\') { j++; continue; }
                if (text[j + 1] == closerChar) return j;
            }
        }
        return -1;
    }

    private static int FindBacktickClose(string text, int from, int runLen)
    {
        var j = from;
        while (j < text.Length)
        {
            if (text[j] == '`')
            {
                var l = 0;
                while (j + l < text.Length && text[j + l] == '`') l++;
                if (l == runLen) return j;
                j += l;
            }
            else j++;
        }
        return -1;
    }

    private static bool IsAtLineStart(string text, int pos)
    {
        for (var j = pos - 1; j >= 0; j--)
        {
            var c = text[j];
            if (c == '\n') return true;
            if (c != ' ' && c != '\t') return false;
        }
        return true;
    }

    private static bool IsAtLineEnd(string text, int pos)
    {
        for (var j = pos; j < text.Length; j++)
        {
            var c = text[j];
            if (c == '\n') return true;
            if (c != ' ' && c != '\t') return false;
        }
        return true;
    }

    private static string TrimNewlines(string s) => s.Trim(' ', '\t', '\n', '\r');
}
