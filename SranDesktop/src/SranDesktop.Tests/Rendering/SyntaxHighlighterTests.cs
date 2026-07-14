using FluentAssertions;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

public class SyntaxHighlighterTests
{
    private static string Concat(IEnumerable<HighlightToken> tokens) =>
        string.Concat(tokens.Select(t => t.Text));

    [Fact]
    public void Keyword_Does_Not_Match_Inside_Identifiers()
    {
        var code = "class Foo { void classify() { } }";
        var tokens = SyntaxHighlighter.Tokenize(code, "csharp")!;

        tokens.Should().Contain(t => t.Text == "class" && t.Kind == TokenKind.Keyword);
        tokens.Should().Contain(t => t.Text == "void" && t.Kind == TokenKind.Type);
        // "classify" must NOT be split into keyword "class" + "ify".
        tokens.Where(t => t.Kind == TokenKind.Keyword).Should()
            .NotContain(t => t.Text == "classify");
        tokens.Should().NotContain(t => t.Text == "ify");
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void String_Containing_Slashes_Is_Not_A_Comment()
    {
        var code = "var url = \"http://example.com\";";
        var tokens = SyntaxHighlighter.Tokenize(code, "javascript")!;

        tokens.Should().Contain(t => t.Text == "\"http://example.com\"" && t.Kind == TokenKind.String);
        tokens.Should().NotContain(t => t.Kind == TokenKind.Comment);
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Comment_Containing_Quotes_Is_One_Comment()
    {
        var code = "int x = 1; // it's \"quoted\" text";
        var tokens = SyntaxHighlighter.Tokenize(code, "csharp")!;

        tokens.Should().Contain(t => t.Kind == TokenKind.Comment
                                     && t.Text == "// it's \"quoted\" text");
        tokens.Should().NotContain(t => t.Kind == TokenKind.String);
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Block_Comment_Spans_Lines()
    {
        var code = "a();\n/* first\nsecond \"x\" */\nb();";
        var tokens = SyntaxHighlighter.Tokenize(code, "csharp")!;

        tokens.Should().Contain(t => t.Kind == TokenKind.Comment
                                     && t.Text == "/* first\nsecond \"x\" */");
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Json_Numbers_Bools_And_Strings_Classify()
    {
        var code = "{\"count\": 12.5, \"on\": true, \"none\": null}";
        var tokens = SyntaxHighlighter.Tokenize(code, "json")!;

        tokens.Should().Contain(t => t.Text == "12.5" && t.Kind == TokenKind.Number);
        tokens.Should().Contain(t => t.Text == "true" && t.Kind == TokenKind.Keyword);
        tokens.Should().Contain(t => t.Text == "null" && t.Kind == TokenKind.Keyword);
        tokens.Should().Contain(t => t.Text == "\"count\"" && t.Kind == TokenKind.String);
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Unknown_Language_Returns_Null_For_Single_Color_Fallback()
    {
        SyntaxHighlighter.Tokenize("whatever ++ --", "brainfuck").Should().BeNull();
        SyntaxHighlighter.Tokenize("text", "").Should().BeNull();
    }

    [Fact]
    public void Python_Triple_Quoted_String_Swallows_Hash()
    {
        var code = "x = \"\"\"docstring with # not a comment\"\"\"\n# real comment";
        var tokens = SyntaxHighlighter.Tokenize(code, "python")!;

        tokens.Should().Contain(t => t.Kind == TokenKind.String
                                     && t.Text.Contains("# not a comment"));
        tokens.Should().Contain(t => t.Kind == TokenKind.Comment && t.Text == "# real comment");
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Sql_Keywords_Are_Case_Insensitive()
    {
        var code = "SELECT id FROM users WHERE name = 'O''Brien' -- note";
        var tokens = SyntaxHighlighter.Tokenize(code, "sql")!;

        tokens.Should().Contain(t => t.Text == "SELECT" && t.Kind == TokenKind.Keyword);
        tokens.Should().Contain(t => t.Text == "FROM" && t.Kind == TokenKind.Keyword);
        // '' doubling stays inside ONE string token.
        tokens.Should().Contain(t => t.Text == "'O''Brien'" && t.Kind == TokenKind.String);
        tokens.Should().Contain(t => t.Text == "-- note" && t.Kind == TokenKind.Comment);
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Go_Backtick_Raw_String_Is_Multiline()
    {
        var code = "s := `line one\nline two`\nreturn s";
        var tokens = SyntaxHighlighter.Tokenize(code, "go")!;

        tokens.Should().Contain(t => t.Kind == TokenKind.String
                                     && t.Text == "`line one\nline two`");
        tokens.Should().Contain(t => t.Text == "return" && t.Kind == TokenKind.Keyword);
        Concat(tokens).Should().Be(code);
    }

    [Fact]
    public void Bash_And_Yaml_Hash_Comments()
    {
        var bash = SyntaxHighlighter.Tokenize("echo hi # done", "bash")!;
        bash.Should().Contain(t => t.Text == "# done" && t.Kind == TokenKind.Comment);
        bash.Should().Contain(t => t.Text == "echo" && t.Kind == TokenKind.Keyword);

        var yaml = SyntaxHighlighter.Tokenize("enabled: true # flag", "yml")!;
        yaml.Should().Contain(t => t.Text == "true" && t.Kind == TokenKind.Keyword);
        yaml.Should().Contain(t => t.Text == "# flag" && t.Kind == TokenKind.Comment);
    }

    [Fact]
    public void Unterminated_String_Stops_At_End_Of_Line()
    {
        // A stray quote mid-stream must not paint the rest of the block.
        var code = "var s = \"oops\nvar next = 1;";
        var tokens = SyntaxHighlighter.Tokenize(code, "javascript")!;

        tokens.Should().Contain(t => t.Kind == TokenKind.String && t.Text == "\"oops");
        tokens.Should().Contain(t => t.Text == "1" && t.Kind == TokenKind.Number);
        Concat(tokens).Should().Be(code);
    }

    [Theory]
    [InlineData("c#", "csharp")]
    [InlineData("PY", "python")]
    [InlineData("ts", "typescript")]
    [InlineData("shell", "bash")]
    [InlineData("golang", "go")]
    [InlineData("html", "xml")]
    public void Language_Aliases_Normalize(string alias, string expected)
    {
        SyntaxHighlighter.Normalize(alias).Should().Be(expected);
    }
}
