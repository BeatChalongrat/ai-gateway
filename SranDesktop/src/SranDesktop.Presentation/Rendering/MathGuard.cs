using Markdig.Extensions.Mathematics;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Streaming guard + classification helpers for math nodes (Phase 12 C).
/// The renderer's pipeline (UseAdvancedExtensions includes Mathematics)
/// already parses $...$ / $$...$$ into MathInline / MathBlock; these
/// predicates decide HOW the renderer surfaces them. This is the math
/// counterpart of the Phase 4 <see cref="StreamingFenceGuard"/>, but it works
/// on PARSED nodes instead of scanning the raw string — so a "$$" inside a
/// code fence can never false-positive: the parser already classified it as
/// code. (A trailing lone "$" or an unclosed "$x" needs no guard at all:
/// Markdig leaves those as literal text, which is exactly the degraded
/// mid-stream rendering we want.)
/// </summary>
public static class MathGuard
{
    /// <summary>An unterminated $$ block (a mid-stream chunk that ended inside
    /// it) has no closing fence yet — Markdig leaves ClosingFencedCharCount 0,
    /// the same signal <see cref="BlockDiff"/> relies on for code fences.</summary>
    public static bool IsClosed(MathBlock block) => block.ClosingFencedCharCount > 0;

    /// <summary>
    /// KaTeX (a WebView2 instance) only renders a block that is complete AND
    /// the stream is done. While streaming, even a CLOSED block stays styled
    /// text: the progressive renderer re-builds the trailing block on every
    /// 150 ms tick, and re-creating browser instances there would churn and
    /// flash. The authoritative post-stream render swaps the text for KaTeX
    /// exactly once.
    /// </summary>
    public static bool ShouldRenderKatex(MathBlock block, bool streaming) =>
        !streaming && IsClosed(block);

    /// <summary>
    /// A paragraph that consists of EXACTLY one $$...$$ inline — the common
    /// single-line display form models emit ("$$E=mc^2$$" without surrounding
    /// blank-line fences) — upgrades to the display-block surface. Returns the
    /// math inline when so, null for every mixed or single-$ paragraph.
    /// </summary>
    public static MathInline? AsDisplayMath(ParagraphBlock paragraph)
    {
        if (paragraph.Inline is null)
            return null;

        Inline? only = null;
        foreach (var inline in paragraph.Inline)
        {
            if (only is not null)
                return null; // more than one inline — mixed content
            only = inline;
        }

        return only is MathInline { DelimiterCount: >= 2 } math ? math : null;
    }
}
