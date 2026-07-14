namespace SranDesktop.Presentation.Rendering;

public enum TokenKind { Plain, Keyword, Type, String, Comment, Number }

public readonly record struct HighlightToken(string Text, TokenKind Kind);

/// <summary>
/// Hand-rolled, dependency-free tokenizer for code-block syntax highlighting.
/// Single pass, O(n): block/line comments, strings (single-line by default;
/// backtick/triple-quoted are multi-line), identifiers classified against
/// per-language keyword/type sets, and simple numeric literals. Deliberately
/// approximate — it colors, it does not parse; the WPF layer maps TokenKind
/// to theme brushes. Unknown languages return null → single-color rendering.
/// </summary>
public static class SyntaxHighlighter
{
    private sealed class Profile
    {
        public HashSet<string> Keywords = new(StringComparer.Ordinal);
        public HashSet<string> Types = new(StringComparer.Ordinal);
        public string[] LineComments = Array.Empty<string>();
        public (string Start, string End)[] BlockComments = Array.Empty<(string, string)>();
        public char[] StringQuotes = { '"', '\'' };
        public bool TripleQuotes;        // python """ / '''
        public bool BacktickStrings;     // js/ts templates, go raw strings (multi-line)
        public bool DoubledQuoteEscape;  // sql: '' escapes inside '…'
    }

    private static readonly Dictionary<string, Profile> Profiles = BuildProfiles();

    /// <summary>Tokenizes <paramref name="code"/>; null when the language is
    /// unknown (caller falls back to single-color rendering). Concatenating
    /// the returned token texts always reproduces the input exactly.</summary>
    public static IReadOnlyList<HighlightToken>? Tokenize(string code, string language)
    {
        var key = Normalize(language);
        if (key is null || !Profiles.TryGetValue(key, out var profile))
            return null;
        return Scan(code, profile);
    }

    internal static string? Normalize(string language)
    {
        var lang = language.Trim().ToLowerInvariant();
        return lang switch
        {
            "cs" or "c#" or "csharp" => "csharp",
            "py" or "python" or "python3" => "python",
            "js" or "javascript" or "jsx" or "node" => "javascript",
            "ts" or "typescript" or "tsx" => "typescript",
            "json" or "jsonc" => "json",
            "xml" or "html" or "xhtml" or "svg" or "xaml" => "xml",
            "sql" or "postgres" or "postgresql" or "mysql" or "sqlite" => "sql",
            "sh" or "bash" or "shell" or "zsh" or "console" => "bash",
            "go" or "golang" => "go",
            "java" => "java",
            "yaml" or "yml" => "yaml",
            "css" or "scss" or "less" => "css",
            _ => null
        };
    }

    // ---------- scanner ----------

    private static List<HighlightToken> Scan(string code, Profile p)
    {
        var tokens = new List<HighlightToken>();
        var plainStart = 0;
        var i = 0;

        void FlushPlain(int end)
        {
            if (end > plainStart)
                tokens.Add(new HighlightToken(code[plainStart..end], TokenKind.Plain));
        }

        while (i < code.Length)
        {
            var c = code[i];

            // Block comments (checked first so "/*" wins over "/" noise).
            var consumed = false;
            foreach (var (start, end) in p.BlockComments)
            {
                if (!Matches(code, i, start))
                    continue;
                FlushPlain(i);
                var close = code.IndexOf(end, i + start.Length, StringComparison.Ordinal);
                var stop = close < 0 ? code.Length : close + end.Length;
                tokens.Add(new HighlightToken(code[i..stop], TokenKind.Comment));
                i = stop;
                plainStart = i;
                consumed = true;
                break;
            }
            if (consumed) continue;

            // Line comments — a string containing "//" never reaches here
            // because the opening quote is consumed as a string first.
            foreach (var marker in p.LineComments)
            {
                if (!Matches(code, i, marker))
                    continue;
                FlushPlain(i);
                var eol = code.IndexOf('\n', i);
                var stop = eol < 0 ? code.Length : eol; // keep the newline plain
                tokens.Add(new HighlightToken(code[i..stop], TokenKind.Comment));
                i = stop;
                plainStart = i;
                consumed = true;
                break;
            }
            if (consumed) continue;

            // Triple-quoted strings (python) — before single quotes.
            if (p.TripleQuotes && (Matches(code, i, "\"\"\"") || Matches(code, i, "'''")))
            {
                var quote = code.Substring(i, 3);
                FlushPlain(i);
                var close = code.IndexOf(quote, i + 3, StringComparison.Ordinal);
                var stop = close < 0 ? code.Length : close + 3;
                tokens.Add(new HighlightToken(code[i..stop], TokenKind.String));
                i = stop;
                plainStart = i;
                continue;
            }

            // Strings (a comment containing quotes never reaches here — the
            // comment was consumed whole above).
            if (Array.IndexOf(p.StringQuotes, c) >= 0 || (p.BacktickStrings && c == '`'))
            {
                FlushPlain(i);
                var stop = ScanString(code, i, c, p);
                tokens.Add(new HighlightToken(code[i..stop], TokenKind.String));
                i = stop;
                plainStart = i;
                continue;
            }

            // Identifiers — read MAXIMALLY, so "classify" is one identifier
            // and never matches the keyword "class".
            if (char.IsLetter(c) || c == '_')
            {
                var start = i;
                while (i < code.Length && (char.IsLetterOrDigit(code[i]) || code[i] is '_' or '$'))
                    i++;
                var word = code[start..i];
                var kind = p.Keywords.Contains(word) ? TokenKind.Keyword
                    : p.Types.Contains(word) ? TokenKind.Type
                    : TokenKind.Plain;
                if (kind != TokenKind.Plain)
                {
                    FlushPlain(start);
                    tokens.Add(new HighlightToken(word, kind));
                    plainStart = i;
                }
                continue;
            }

            // Numbers (digits inside identifiers were consumed above).
            if (char.IsDigit(c))
            {
                var start = i;
                while (i < code.Length && (char.IsLetterOrDigit(code[i]) || code[i] is '.' or '_'))
                    i++;
                FlushPlain(start);
                tokens.Add(new HighlightToken(code[start..i], TokenKind.Number));
                plainStart = i;
                continue;
            }

            i++;
        }

        FlushPlain(code.Length);
        return tokens;
    }

    private static int ScanString(string code, int start, char quote, Profile p)
    {
        var i = start + 1;
        while (i < code.Length)
        {
            var c = code[i];
            if (c == '\\' && !p.DoubledQuoteEscape && i + 1 < code.Length)
            {
                i += 2;
                continue;
            }
            if (c == quote)
            {
                if (p.DoubledQuoteEscape && i + 1 < code.Length && code[i + 1] == quote)
                {
                    i += 2;
                    continue;
                }
                return i + 1;
            }
            // Unterminated single-line string stops at EOL so one stray quote
            // mid-stream can't paint the rest of the block as a string.
            // Backtick (template/raw) strings are legitimately multi-line.
            if (c == '\n' && quote != '`')
                return i;
            i++;
        }
        return code.Length;
    }

    private static bool Matches(string code, int index, string marker) =>
        code.AsSpan(index).StartsWith(marker, StringComparison.Ordinal);

    // ---------- language profiles ----------

    private static Dictionary<string, Profile> BuildProfiles()
    {
        var slashComments = new[] { ("/*", "*/") };

        var profiles = new Dictionary<string, Profile>(StringComparer.Ordinal)
        {
            ["csharp"] = new Profile
            {
                Keywords = Words(
                    "abstract as base break case catch checked class const continue default delegate do else " +
                    "enum event explicit extern false finally fixed for foreach goto if implicit in interface " +
                    "internal is lock namespace new null operator out override params private protected public " +
                    "readonly record ref return sealed sizeof stackalloc static struct switch this throw true " +
                    "try typeof unchecked unsafe using virtual volatile while async await when where yield init " +
                    "required get set value nameof partial"),
                Types = Words("bool byte char decimal double dynamic float int long object sbyte short string uint ulong ushort var void"),
                LineComments = new[] { "//" },
                BlockComments = slashComments,
            },
            ["python"] = new Profile
            {
                Keywords = Words(
                    "and as assert async await break class continue def del elif else except finally for from " +
                    "global if import in is lambda nonlocal not or pass raise return try while with yield " +
                    "True False None self match case"),
                Types = Words("int float str bool list dict set tuple bytes object type"),
                LineComments = new[] { "#" },
                TripleQuotes = true,
            },
            ["javascript"] = new Profile
            {
                Keywords = Words(
                    "break case catch class const continue debugger default delete do else export extends " +
                    "finally for function if import in instanceof let new of return static super switch this " +
                    "throw try typeof var void while with yield async await true false null undefined get set from"),
                LineComments = new[] { "//" },
                BlockComments = slashComments,
                BacktickStrings = true,
            },
            ["typescript"] = new Profile
            {
                Keywords = Words(
                    "break case catch class const continue debugger default delete do else export extends " +
                    "finally for function if import in instanceof let new of return static super switch this " +
                    "throw try typeof var void while with yield async await true false null undefined get set " +
                    "from interface type enum implements declare namespace readonly abstract public private " +
                    "protected satisfies keyof infer as is"),
                Types = Words("string number boolean any void never unknown object symbol bigint"),
                LineComments = new[] { "//" },
                BlockComments = slashComments,
                BacktickStrings = true,
            },
            ["json"] = new Profile
            {
                Keywords = Words("true false null"),
                StringQuotes = new[] { '"' },
                LineComments = new[] { "//" },   // tolerate jsonc
                BlockComments = slashComments,
            },
            ["xml"] = new Profile
            {
                BlockComments = new[] { ("<!--", "-->") },
            },
            ["sql"] = new Profile
            {
                Keywords = WordsIgnoreCase(
                    "select from where insert into values update set delete create table alter drop index view " +
                    "join inner left right outer full cross on as and or not null primary key foreign references " +
                    "group by order having limit offset distinct union all exists between like ilike in is case " +
                    "when then else end begin commit rollback transaction returning default constraint unique " +
                    "cascade if add column"),
                Types = WordsIgnoreCase(
                    "int integer bigint smallint serial bigserial text varchar char boolean bool numeric decimal " +
                    "real float double timestamp timestamptz date time interval uuid json jsonb bytea"),
                LineComments = new[] { "--" },
                BlockComments = slashComments,
                StringQuotes = new[] { '\'' },   // "…" is an identifier in SQL — leave plain
                DoubledQuoteEscape = true,
            },
            ["bash"] = new Profile
            {
                Keywords = Words(
                    "if then else elif fi for while until do done case esac function in select return exit " +
                    "break continue local export readonly declare unset shift eval exec set trap source alias " +
                    "echo cd true false"),
                LineComments = new[] { "#" },
            },
            ["go"] = new Profile
            {
                Keywords = Words(
                    "break case chan const continue default defer else fallthrough for func go goto if import " +
                    "interface map package range return select struct switch type var nil true false iota make new len cap append"),
                Types = Words(
                    "bool byte complex64 complex128 error float32 float64 int int8 int16 int32 int64 rune " +
                    "string uint uint8 uint16 uint32 uint64 uintptr any"),
                LineComments = new[] { "//" },
                BlockComments = slashComments,
                BacktickStrings = true,          // raw string literals
            },
            ["java"] = new Profile
            {
                Keywords = Words(
                    "abstract assert break case catch class const continue default do else enum extends final " +
                    "finally for goto if implements import instanceof interface native new package private " +
                    "protected public return static strictfp super switch synchronized this throw throws " +
                    "transient try volatile while true false null var record sealed permits yield"),
                Types = Words("boolean byte char double float int long short void String Integer Long Double Boolean Object List Map"),
                LineComments = new[] { "//" },
                BlockComments = slashComments,
            },
            ["yaml"] = new Profile
            {
                Keywords = Words("true false null yes no on off"),
                LineComments = new[] { "#" },
            },
            ["css"] = new Profile
            {
                Keywords = Words("important inherit initial unset none auto flex grid block inline absolute relative fixed sticky solid hidden"),
                BlockComments = slashComments,
            },
        };
        return profiles;
    }

    private static HashSet<string> Words(string spaceSeparated) =>
        new(spaceSeparated.Split(' ', StringSplitOptions.RemoveEmptyEntries), StringComparer.Ordinal);

    private static HashSet<string> WordsIgnoreCase(string spaceSeparated) =>
        new(spaceSeparated.Split(' ', StringSplitOptions.RemoveEmptyEntries), StringComparer.OrdinalIgnoreCase);
}
