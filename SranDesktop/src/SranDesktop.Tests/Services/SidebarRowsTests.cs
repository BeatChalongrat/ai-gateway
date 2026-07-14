using System.Collections.ObjectModel;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 8 Task 2: flat sidebar row ordering (pure builder).</summary>
public class SidebarRowsBuilderTests
{
    private static ConversationItemViewModel Conv(
        string id, string title, DateTimeOffset updatedAt,
        bool pinned = false, string? folderId = null) =>
        new(new Conversation
        {
            Id = id,
            Title = title,
            CreatedAt = updatedAt,
            UpdatedAt = updatedAt,
            IsPinned = pinned,
            FolderId = folderId,
            Source = ConversationSource.Server
        });

    private static FolderItemViewModel Folder(string id, string name) =>
        new(new FolderDto(id, name, 0, 0, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow),
            new ObservableCollection<ConversationItemViewModel>(), _ => true);

    private static List<object> Build(
        IReadOnlyList<FolderItemViewModel> folders,
        IReadOnlyList<ConversationItemViewModel> conversations,
        IReadOnlyList<MessageSearchHitViewModel>? hits = null,
        bool showSearch = false,
        Func<ConversationItemViewModel, bool>? match = null) =>
        SidebarRowsBuilder.Build(
            folders, conversations, match ?? (_ => true),
            hits ?? new List<MessageSearchHitViewModel>(), showSearch);

    [Fact]
    public void Folders_Come_First_Then_Grouped_Buckets()
    {
        var now = DateTimeOffset.Now;
        var folder = Folder("f1", "Work");
        folder.IsExpanded = true;
        var filed = Conv("c1", "Filed", now, folderId: "f1");
        var pinned = Conv("c2", "Pinned", now.AddDays(-40), pinned: true);
        var today = Conv("c3", "Today", now);

        var rows = Build(new[] { folder }, new[] { filed, pinned, today });

        rows[0].Should().BeSameAs(folder);
        rows[1].Should().BeOfType<FolderConversationRow>()
            .Which.Conversation.Should().BeSameAs(filed);
        // Pinned bucket header (rank 0) precedes Today's (rank 1).
        rows[2].Should().BeOfType<SidebarGroupHeaderRow>();
        rows[3].Should().BeSameAs(pinned);
        rows[4].Should().BeOfType<SidebarGroupHeaderRow>();
        rows[5].Should().BeSameAs(today);
    }

    [Fact]
    public void Collapsed_Folder_Hides_Its_Members()
    {
        var now = DateTimeOffset.Now;
        var folder = Folder("f1", "Work");
        folder.IsExpanded = false;
        var filed = Conv("c1", "Filed", now, folderId: "f1");

        var rows = Build(new[] { folder }, new[] { filed });

        rows.Should().ContainSingle().Which.Should().BeSameAs(folder);
    }

    [Fact]
    public void Unknown_Folder_Id_Falls_Back_To_Time_Buckets()
    {
        // Offline/stale folder id — the chat must never disappear.
        var conv = Conv("c1", "Orphan", DateTimeOffset.Now, folderId: "gone");

        var rows = Build(Array.Empty<FolderItemViewModel>(), new[] { conv });

        rows.Should().HaveCount(2); // header + row
        rows[1].Should().BeSameAs(conv);
    }

    [Fact]
    public void Newest_First_Within_A_Bucket()
    {
        var now = DateTimeOffset.Now;
        var older = Conv("c1", "Older", now.AddHours(-2));
        var newer = Conv("c2", "Newer", now);

        var rows = Build(Array.Empty<FolderItemViewModel>(), new[] { older, newer });

        rows.OfType<ConversationItemViewModel>().Should().Equal(newer, older);
    }

    [Fact]
    public void Search_Filter_Applies_To_Both_Sections()
    {
        var now = DateTimeOffset.Now;
        var folder = Folder("f1", "Work");
        var filedMatch = Conv("c1", "alpha report", now, folderId: "f1");
        var filedMiss = Conv("c2", "beta", now, folderId: "f1");
        var looseMiss = Conv("c3", "gamma", now);

        var rows = Build(new[] { folder }, new[] { filedMatch, filedMiss, looseMiss },
            match: c => c.DisplayTitle.Contains("alpha"));

        rows.OfType<FolderConversationRow>().Should().ContainSingle()
            .Which.Conversation.Should().BeSameAs(filedMatch);
        rows.OfType<ConversationItemViewModel>().Should().BeEmpty();
    }

    [Fact]
    public void Search_Section_Renders_Header_Then_Hits()
    {
        var hit = new MessageSearchHitViewModel("c9", "Title", "…snippet…");

        var rows = Build(Array.Empty<FolderItemViewModel>(),
            Array.Empty<ConversationItemViewModel>(),
            hits: new[] { hit }, showSearch: true);

        rows[0].Should().BeOfType<SidebarSearchHeaderRow>();
        rows[1].Should().BeSameAs(hit);
    }
}

/// <summary>Phase 8 Task 2: pure drop policy.</summary>
public class SidebarDropDecisionTests
{
    [Fact]
    public void Drop_On_A_Different_Folder_Moves()
    {
        var result = SidebarDropDecision.Decide("c1", "f1", SidebarDropTargetKind.Folder, "f2");
        result.ShouldMove.Should().BeTrue();
        result.TargetFolderId.Should().Be("f2");
    }

    [Fact]
    public void Drop_On_The_Current_Folder_Is_A_NoOp()
    {
        SidebarDropDecision.Decide("c1", "f1", SidebarDropTargetKind.Folder, "f1")
            .ShouldMove.Should().BeFalse();
    }

    [Fact]
    public void Unfiled_Chat_Onto_A_Folder_Moves()
    {
        var result = SidebarDropDecision.Decide("c1", null, SidebarDropTargetKind.Folder, "f1");
        result.Should().Be(new SidebarDropResult(true, "f1"));
    }

    [Fact]
    public void Unfile_Drop_Moves_Out_Only_When_Filed()
    {
        SidebarDropDecision.Decide("c1", "f1", SidebarDropTargetKind.Unfile, null)
            .Should().Be(new SidebarDropResult(true, null));
        SidebarDropDecision.Decide("c1", null, SidebarDropTargetKind.Unfile, null)
            .ShouldMove.Should().BeFalse();
    }

    [Fact]
    public void Hostile_Or_Empty_Inputs_Are_NoOps()
    {
        SidebarDropDecision.Decide("", "f1", SidebarDropTargetKind.Folder, "f2")
            .ShouldMove.Should().BeFalse();
        SidebarDropDecision.Decide("c1", "f1", SidebarDropTargetKind.Folder, "")
            .ShouldMove.Should().BeFalse();
        SidebarDropDecision.Decide("c1", "f1", SidebarDropTargetKind.Folder, null)
            .ShouldMove.Should().BeFalse();
    }
}
