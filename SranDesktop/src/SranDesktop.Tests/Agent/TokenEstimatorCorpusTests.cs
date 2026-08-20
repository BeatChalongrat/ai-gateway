using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// The desktop half of the SHARED estimator corpus. The Go suite
/// (services/chat-edge-api/internal/service/token_estimator_corpus_test.go)
/// asserts the same expectations against the same file, so neither
/// estimator can change its model without breaking the other side's tests.
/// </summary>
public class TokenEstimatorCorpusTests
{
    private sealed record CorpusCase(string Name, string Text, int Tokens, string? Note);

    private sealed record CorpusFile(List<CorpusCase> Cases);

    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web);

    /// <summary>
    /// Walk up from the test assembly to the repo root. The test binary sits
    /// under SranDesktop/src/SranDesktop.Tests/bin/&lt;cfg&gt;/&lt;tfm&gt;/, and the
    /// fixture is shared with a Go suite outside the desktop solution, so a
    /// relative hop would be brittle — search instead and fail loudly.
    /// </summary>
    private static string CorpusPath()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var candidate = Path.Combine(
                dir.FullName, "tests", "fixtures", "token_estimator_corpus.json");
            if (File.Exists(candidate))
                return candidate;
            dir = dir.Parent;
        }

        throw new FileNotFoundException(
            "Shared corpus tests/fixtures/token_estimator_corpus.json not found "
            + $"walking up from {AppContext.BaseDirectory}.");
    }

    private static List<CorpusCase> Load()
    {
        var json = File.ReadAllText(CorpusPath());
        var parsed = JsonSerializer.Deserialize<CorpusFile>(json, JsonOptions);
        parsed.Should().NotBeNull();
        parsed!.Cases.Should().NotBeEmpty("the shared corpus must carry cases");
        return parsed.Cases;
    }

    public static TheoryData<string, string, int, string?> Cases()
    {
        var data = new TheoryData<string, string, int, string?>();
        foreach (var c in Load())
            data.Add(c.Name, c.Text, c.Tokens, c.Note);
        return data;
    }

    [Theory]
    [MemberData(nameof(Cases))]
    public void Matches_The_Shared_Corpus(string name, string text, int tokens, string? note)
    {
        TokenEstimator.EstimateText(text).Should().Be(
            tokens,
            "shared corpus case '{0}' pins this value for BOTH clients ({1})", name, note);
    }

    [Fact]
    public void Corpus_Covers_The_Scripts_We_Care_About()
    {
        var names = Load().Select(c => c.Name).ToHashSet();

        names.Should().Contain(new[]
        {
            "empty", "pure_ascii", "pure_thai", "mixed_thai_ascii",
            "thai_numerals_and_punctuation", "emoji", "cjk"
        });
    }

    [Fact]
    public void Thai_Now_Costs_Far_More_Than_The_Old_Flat_Model()
    {
        // The regression this item exists for: 27 Thai runes + a space used
        // to estimate ceil(28/4) = 7 tokens; the server has always said 18.
        const string thai = "สวัสดีครับ ยินดีที่ได้รู้จัก";

        var estimated = TokenEstimator.EstimateText(thai);
        var oldFlatModel = (thai.Length + 3) / 4;

        estimated.Should().Be(18);
        estimated.Should().BeGreaterThan(oldFlatModel * 2,
            "the whole point is that Thai was undercounted roughly 2.7x");
    }

    [Fact]
    public void Astral_Emoji_Count_As_One_Code_Point_Not_Two_Utf16_Units()
    {
        // "🎉" is one code point, two UTF-16 chars. An implementation that
        // walks string.Length disagrees with the server here.
        const string emoji = "🎉🎉🎉 great job 🚀";

        emoji.Length.Should().Be(19, "UTF-16 units, including 4 surrogate pairs");
        TokenEstimator.EstimateText(emoji).Should().Be(3, "15 code points, floor(15/4)");
    }
}
