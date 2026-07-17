using System.Collections.Specialized;
using System.Windows.Input;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// FIX 12: SidebarRows mixes 6 row types in one recycled ItemsControl, and
/// WPF transiently requeries commands with a wrong-type DataContext during
/// rebinds. CommunityToolkit's RelayCommand&lt;T&gt; THROWS on a non-null
/// wrong-type parameter — the live "Something went wrong" crash. Every
/// sidebar-bound command must treat a wrong-type parameter as a silent no-op
/// in BOTH CanExecute and Execute.
/// </summary>
public class SidebarCommandTypeSafetyTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<IConversationStore> _store = new();
    private readonly Mock<IDialogService> _dialogs = new();

    private ConversationListViewModel Create()
    {
        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());
        return new ConversationListViewModel(
            _api.Object, _store.Object, Mock.Of<IConversationSyncService>(),
            Mock.Of<IAgentConversationSyncService>(), settings.Object,
            _dialogs.Object, NullLogger<ConversationListViewModel>.Instance);
    }

    private static ConversationItemViewModel Conv(string id = "c1", string title = "Chat") =>
        new(new Conversation
        {
            Id = id, Title = title, CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-5),
            UpdatedAt = DateTimeOffset.UtcNow, Source = ConversationSource.Server
        });

    /// <summary>The wrong-type rows WPF can transiently hand any sidebar
    /// button while containers rebind across templates.</summary>
    private static IEnumerable<object> WrongTypeRows()
    {
        yield return new SidebarGroupHeaderRow("TODAY");
        yield return new SidebarSearchHeaderRow();
        yield return new FolderConversationRow(Conv("wrapped"));
        yield return "an arbitrary string row";
    }

    [Fact]
    public void Every_Sidebar_Command_Tolerates_Wrong_Type_Parameters()
    {
        var vm = Create();
        var commands = new (string Name, ICommand Command)[]
        {
            (nameof(vm.SelectConversationCommand), vm.SelectConversationCommand),
            (nameof(vm.TogglePinCommand), vm.TogglePinCommand),
            (nameof(vm.RenameCommand), vm.RenameCommand),
            (nameof(vm.DeleteCommand), vm.DeleteCommand),
            (nameof(vm.OpenMessageHitCommand), vm.OpenMessageHitCommand),
            (nameof(vm.RenameFolderCommand), vm.RenameFolderCommand),
            (nameof(vm.DeleteFolderCommand), vm.DeleteFolderCommand),
            (nameof(vm.MoveToNewFolderCommand), vm.MoveToNewFolderCommand),
        };

        foreach (var (name, command) in commands)
        {
            foreach (var row in WrongTypeRows())
            {
                // The crash was CanExecute throwing ArgumentException during
                // ButtonBase.UpdateCanExecute — it must never throw again.
                var canExecute = () => command.CanExecute(row);
                canExecute.Should().NotThrow($"{name}.CanExecute must tolerate {row.GetType().Name}");

                var execute = () => command.Execute(row);
                execute.Should().NotThrow($"{name}.Execute must tolerate {row.GetType().Name}");
            }
        }

        // The no-ops really were no-ops: nothing selected, no confirmation
        // dialog, no API/store traffic.
        vm.SelectedConversation.Should().BeNull();
        _dialogs.Verify(d => d.ConfirmAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()), Times.Never);
        _api.VerifyNoOtherCalls();
        _store.VerifyNoOtherCalls();
    }

    [Fact]
    public void Conversation_Commands_Still_Work_With_The_Right_Type()
    {
        var vm = Create();
        var conv = Conv();
        vm.Conversations.Add(conv);

        vm.SelectConversationCommand.Execute(conv);
        vm.SelectedConversation.Should().BeSameAs(conv);

        vm.RenameCommand.Execute(conv);
        conv.IsRenaming.Should().BeTrue();
        conv.EditingName.Should().Be("Chat");
    }

    [Fact]
    public void Rebuild_With_Unchanged_Content_Raises_No_Collection_Events()
    {
        // FIX 12 Part B: the old Clear()+Add-all rebind-stormed the list on
        // every refresh. An element-equal rebuild must now be a no-op.
        var vm = Create();
        vm.Conversations.Add(Conv("c1", "First"));
        vm.Conversations.Add(Conv("c2", "Second"));
        vm.RebuildSidebarRowsNow();
        var baseline = vm.SidebarRows.ToList();
        baseline.Should().NotBeEmpty();

        var events = 0;
        vm.SidebarRows.CollectionChanged += (_, _) => events++;
        vm.RebuildSidebarRowsNow();

        events.Should().Be(0, "an element-equal rebuild must not touch the collection");
        vm.SidebarRows.Should().Equal(baseline);
    }

    [Fact]
    public void Rebuild_Applies_Real_Changes_In_Place()
    {
        var vm = Create();
        vm.Conversations.Add(Conv("c1", "First"));
        vm.RebuildSidebarRowsNow();

        var events = new List<NotifyCollectionChangedAction>();
        vm.SidebarRows.CollectionChanged += (_, e) => events.Add(e.Action);

        vm.Conversations.Add(Conv("c2", "Second"));
        vm.RebuildSidebarRowsNow();

        // The new conversation is in the rows, and no Reset/Clear happened —
        // only in-place adds/replaces.
        vm.SidebarRows.OfType<ConversationItemViewModel>().Should().HaveCount(2);
        events.Should().NotBeEmpty();
        events.Should().NotContain(NotifyCollectionChangedAction.Reset);
    }
}
