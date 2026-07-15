using System.Text.RegularExpressions;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Conservative TeX → readable-text conversion for INLINE math ($x^2$) and
/// for display blocks while they stream. Inline math renders as a styled Run,
/// never a WebView2 — a chat message can carry dozens of inline formulas, and
/// a browser instance per formula is the tradeoff Phase 12 C rejected (see
/// <see cref="Controls.MathBlockControl"/> for the display-block surface).
/// Known commands become their Unicode symbol; anything unknown stays literal
/// so no content is ever lost.
/// </summary>
public static class MathTex
{
    public static string ToInlineText(string tex)
    {
        if (string.IsNullOrWhiteSpace(tex))
            return string.Empty;

        var s = tex.Trim();

        // ^\circ / ^{\circ} → ° before the generic passes see them.
        s = DegreeRegex.Replace(s, "°");

        // Unwrap text-mode / font wrappers (two passes cover one nesting
        // level); \mathbb single letters map to their double-struck form.
        for (var i = 0; i < 2 && WrapperRegex.IsMatch(s); i++)
            s = WrapperRegex.Replace(s, static m => UnwrapWrapper(m));

        // Simple one-level fractions and roots.
        s = FracRegex.Replace(s, static m => FormatFraction(m));
        s = SqrtRegex.Replace(s, static m => FormatSqrt(m));

        // \commands and single-char escapes.
        s = CommandRegex.Replace(s, static m => ReplaceCommand(m));

        // Super/subscripts — only when EVERY char has a Unicode equivalent.
        s = SuperscriptRegex.Replace(s, static m => MapScript(m, Superscripts));
        s = SubscriptRegex.Replace(s, static m => MapScript(m, Subscripts));

        s = SpaceRunRegex.Replace(s, " ");
        return s.Trim();
    }

    private static readonly Regex DegreeRegex =
        new(@"\^\{?\\circ\}?", RegexOptions.Compiled);

    private static readonly Regex WrapperRegex = new(
        @"\\(text|textrm|textit|textbf|mathrm|mathbf|mathit|mathsf|mathcal|mathbb|operatorname)\{([^{}]*)\}",
        RegexOptions.Compiled);

    private static readonly Regex FracRegex =
        new(@"\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}", RegexOptions.Compiled);

    private static readonly Regex SqrtRegex =
        new(@"\\sqrt\{([^{}]+)\}", RegexOptions.Compiled);

    private static readonly Regex CommandRegex =
        new(@"\\([a-zA-Z]+|.)", RegexOptions.Compiled);

    private static readonly Regex SuperscriptRegex =
        new(@"\^(?:\{([^{}]+)\}|(\S))", RegexOptions.Compiled);

    private static readonly Regex SubscriptRegex =
        new(@"_(?:\{([^{}]+)\}|(\S))", RegexOptions.Compiled);

    private static readonly Regex SpaceRunRegex = new(@" {2,}", RegexOptions.Compiled);

    /// <summary>A fraction part simple enough to print as "a/b" without parens.</summary>
    private static readonly Regex AtomRegex = new(@"^\\?[A-Za-z0-9]+$", RegexOptions.Compiled);

    private static string UnwrapWrapper(Match m)
    {
        var content = m.Groups[2].Value;
        if (m.Groups[1].Value == "mathbb" && content.Length == 1)
        {
            var mapped = content[0] switch
            {
                'R' => "ℝ", 'N' => "ℕ", 'Z' => "ℤ", 'Q' => "ℚ",
                'C' => "ℂ", 'P' => "ℙ", 'H' => "ℍ",
                _ => content
            };
            return mapped;
        }
        return content;
    }

    private static string FormatFraction(Match m)
    {
        var numerator = m.Groups[1].Value;
        var denominator = m.Groups[2].Value;
        var n = AtomRegex.IsMatch(numerator) ? numerator : $"({numerator})";
        var d = AtomRegex.IsMatch(denominator) ? denominator : $"({denominator})";
        return $"{n}/{d}";
    }

    private static string FormatSqrt(Match m)
    {
        var radicand = m.Groups[1].Value;
        return AtomRegex.IsMatch(radicand) ? $"√{radicand}" : $"√({radicand})";
    }

    private static string ReplaceCommand(Match m)
    {
        var name = m.Groups[1].Value;
        if (Commands.TryGetValue(name, out var symbol))
            return symbol;
        if (name.Length == 1 && !char.IsLetter(name[0]))
        {
            return name[0] switch
            {
                ',' or ';' or ':' or ' ' => " ",
                '!' => "",
                '\\' => " ",
                '{' or '}' or '$' or '%' or '&' or '#' or '_' or '|' => name,
                _ => m.Value
            };
        }
        return m.Value; // unknown command — keep literal, lose nothing
    }

    private static string MapScript(Match m, IReadOnlyDictionary<char, char> map)
    {
        var candidate = m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value;
        Span<char> mapped = candidate.Length <= 64 ? stackalloc char[candidate.Length] : new char[candidate.Length];
        for (var i = 0; i < candidate.Length; i++)
        {
            if (!map.TryGetValue(candidate[i], out var c))
                return m.Value; // something unmappable — keep the TeX as-is
            mapped[i] = c;
        }
        return new string(mapped);
    }

    private static readonly Dictionary<char, char> Superscripts = new()
    {
        ['0'] = '⁰', ['1'] = '¹', ['2'] = '²', ['3'] = '³', ['4'] = '⁴',
        ['5'] = '⁵', ['6'] = '⁶', ['7'] = '⁷', ['8'] = '⁸', ['9'] = '⁹',
        ['+'] = '⁺', ['-'] = '⁻', ['='] = '⁼', ['('] = '⁽', [')'] = '⁾',
        ['n'] = 'ⁿ', ['i'] = 'ⁱ',
    };

    private static readonly Dictionary<char, char> Subscripts = new()
    {
        ['0'] = '₀', ['1'] = '₁', ['2'] = '₂', ['3'] = '₃', ['4'] = '₄',
        ['5'] = '₅', ['6'] = '₆', ['7'] = '₇', ['8'] = '₈', ['9'] = '₉',
        ['+'] = '₊', ['-'] = '₋', ['='] = '₌', ['('] = '₍', [')'] = '₎',
        ['a'] = 'ₐ', ['e'] = 'ₑ', ['h'] = 'ₕ', ['i'] = 'ᵢ', ['j'] = 'ⱼ',
        ['k'] = 'ₖ', ['l'] = 'ₗ', ['m'] = 'ₘ', ['n'] = 'ₙ', ['o'] = 'ₒ',
        ['p'] = 'ₚ', ['r'] = 'ᵣ', ['s'] = 'ₛ', ['t'] = 'ₜ', ['u'] = 'ᵤ',
        ['v'] = 'ᵥ', ['x'] = 'ₓ',
    };

    private static readonly Dictionary<string, string> Commands = new(StringComparer.Ordinal)
    {
        // Greek (lowercase)
        ["alpha"] = "α", ["beta"] = "β", ["gamma"] = "γ", ["delta"] = "δ",
        ["epsilon"] = "ε", ["varepsilon"] = "ε", ["zeta"] = "ζ", ["eta"] = "η",
        ["theta"] = "θ", ["vartheta"] = "ϑ", ["iota"] = "ι", ["kappa"] = "κ",
        ["lambda"] = "λ", ["mu"] = "μ", ["nu"] = "ν", ["xi"] = "ξ",
        ["pi"] = "π", ["rho"] = "ρ", ["sigma"] = "σ", ["tau"] = "τ",
        ["upsilon"] = "υ", ["phi"] = "φ", ["varphi"] = "φ", ["chi"] = "χ",
        ["psi"] = "ψ", ["omega"] = "ω",
        // Greek (uppercase)
        ["Gamma"] = "Γ", ["Delta"] = "Δ", ["Theta"] = "Θ", ["Lambda"] = "Λ",
        ["Xi"] = "Ξ", ["Pi"] = "Π", ["Sigma"] = "Σ", ["Upsilon"] = "ϒ",
        ["Phi"] = "Φ", ["Psi"] = "Ψ", ["Omega"] = "Ω",
        // Operators / relations
        ["times"] = "×", ["cdot"] = "⋅", ["div"] = "÷", ["pm"] = "±", ["mp"] = "∓",
        ["leq"] = "≤", ["le"] = "≤", ["geq"] = "≥", ["ge"] = "≥",
        ["neq"] = "≠", ["ne"] = "≠", ["approx"] = "≈", ["equiv"] = "≡",
        ["sim"] = "∼", ["propto"] = "∝", ["infty"] = "∞",
        ["partial"] = "∂", ["nabla"] = "∇", ["sqrt"] = "√",
        ["sum"] = "∑", ["prod"] = "∏", ["int"] = "∫", ["oint"] = "∮",
        // Sets / logic
        ["in"] = "∈", ["notin"] = "∉", ["subset"] = "⊂", ["subseteq"] = "⊆",
        ["supset"] = "⊃", ["supseteq"] = "⊇", ["cup"] = "∪", ["cap"] = "∩",
        ["forall"] = "∀", ["exists"] = "∃", ["emptyset"] = "∅", ["varnothing"] = "∅",
        ["neg"] = "¬", ["land"] = "∧", ["wedge"] = "∧", ["lor"] = "∨", ["vee"] = "∨",
        ["setminus"] = "∖", ["mid"] = "|",
        // Arrows
        ["to"] = "→", ["rightarrow"] = "→", ["Rightarrow"] = "⇒", ["implies"] = "⇒",
        ["leftarrow"] = "←", ["Leftarrow"] = "⇐", ["leftrightarrow"] = "↔",
        ["Leftrightarrow"] = "⇔", ["iff"] = "⇔", ["mapsto"] = "↦",
        // Dots / misc
        ["ldots"] = "…", ["cdots"] = "⋯", ["dots"] = "…", ["circ"] = "∘",
        ["degree"] = "°", ["angle"] = "∠", ["perp"] = "⊥", ["parallel"] = "∥",
        ["star"] = "⋆", ["oplus"] = "⊕", ["otimes"] = "⊗",
        ["langle"] = "⟨", ["rangle"] = "⟩", ["hbar"] = "ℏ", ["ell"] = "ℓ",
        ["Re"] = "ℜ", ["Im"] = "ℑ", ["prime"] = "′",
        // Spacing / grouping (dropped or collapsed)
        ["left"] = "", ["right"] = "", ["quad"] = " ", ["qquad"] = " ",
        ["displaystyle"] = "", ["textstyle"] = "", ["limits"] = "", ["nolimits"] = "",
    };
}
