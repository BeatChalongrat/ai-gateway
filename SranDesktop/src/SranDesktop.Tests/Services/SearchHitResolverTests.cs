using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

public class SearchHitResolverTests
{
    private static readonly IReadOnlyList<(string Id, string? ServerId)> Locals = new[]
    {
        ("srv-web-1", (string?)null),        // server-source conversation
        ("local-agent-1", "srv-mirror-1"),   // agent chat with a Phase 3 mirror
        ("local-agent-2", (string?)null),    // unsynced agent chat
    };

    [Fact]
    public void Direct_Server_Id_Match_Opens_That_Conversation()
    {
        SearchHitResolver.ResolveLocalConversationId("srv-web-1", Locals)
            .Should().Be("srv-web-1");
    }

    [Fact]
    public void Mirror_Hit_Opens_The_LOCAL_Conversation()
    {
        SearchHitResolver.ResolveLocalConversationId("srv-mirror-1", Locals)
            .Should().Be("local-agent-1");
    }

    [Fact]
    public void Unknown_Server_Conversation_Is_Ignored()
    {
        SearchHitResolver.ResolveLocalConversationId("srv-never-pulled", Locals)
            .Should().BeNull();
    }

    [Fact]
    public void Direct_Match_Wins_Over_Mirror_Match()
    {
        // Pathological overlap: some row's ServerId equals another row's Id —
        // the direct (server-source) row wins deterministically.
        var locals = new[]
        {
            ("local-x", (string?)"srv-a"),
            ("srv-a", (string?)null),
        };

        SearchHitResolver.ResolveLocalConversationId("srv-a", locals)
            .Should().Be("srv-a");
    }

    // ── Phase 8 Task 6: three-way Resolve ───────────────────────────────────

    [Fact]
    public void Resolve_Direct_Match_Is_OpenLocal()
    {
        SearchHitResolver.Resolve("srv-web-1", Locals)
            .Should().Be(new SearchHitResolution(SearchHitAction.OpenLocal, "srv-web-1"));
    }

    [Fact]
    public void Resolve_Mirror_Match_Is_OpenLocal_On_The_LOCAL_Id()
    {
        SearchHitResolver.Resolve("srv-mirror-1", Locals)
            .Should().Be(new SearchHitResolution(SearchHitAction.OpenLocal, "local-agent-1"));
    }

    [Fact]
    public void Resolve_Unknown_Server_Conversation_Is_PullThenOpen()
    {
        // Previously hidden (case (c) returned null) — now the hit shows and
        // clicking pulls the SERVER conversation id.
        SearchHitResolver.Resolve("srv-never-pulled", Locals)
            .Should().Be(new SearchHitResolution(
                SearchHitAction.PullThenOpen, "srv-never-pulled"));
    }
}
