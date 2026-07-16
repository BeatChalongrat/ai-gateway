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

/// <summary>Phase C.5: replacing native MessageBox with an injected
/// IDialogService makes the confirm-gates-delete flow unit-testable — the real
/// testability win the modal MessageBox blocked.</summary>
public class ConfirmFlowTests
{
    private static (ConversationListViewModel Vm, Mock<IDialogService> Dialogs, Mock<IChatEdgeApiClient> Api)
        Create()
    {
        var api = new Mock<IChatEdgeApiClient>();
        var store = new Mock<IConversationStore>();
        var sync = new Mock<IConversationSyncService>();
        var agentSync = new Mock<IAgentConversationSyncService>();
        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());
        var dialogs = new Mock<IDialogService>();

        var vm = new ConversationListViewModel(
            api.Object, store.Object, sync.Object, agentSync.Object,
            settings.Object, dialogs.Object, NullLogger<ConversationListViewModel>.Instance);
        return (vm, dialogs, api);
    }

    private static ConversationItemViewModel Item() =>
        new(new Conversation
        {
            Id = "c1", Title = "Doomed chat",
            CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Server // exercises the server delete path

        });

    [Fact]
    public async Task Delete_Does_Nothing_When_Confirmation_Is_Declined()
    {
        var (vm, dialogs, api) = Create();
        dialogs.Setup(d => d.ConfirmAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()))
            .ReturnsAsync(false); // user clicked Cancel

        await vm.DeleteCommand.ExecuteAsync(Item());

        dialogs.Verify(d => d.ConfirmAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()), Times.Once);
        api.Verify(a => a.DeleteConversationAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Delete_Proceeds_When_Confirmed()
    {
        var (vm, dialogs, api) = Create();
        dialogs.Setup(d => d.ConfirmAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()))
            .ReturnsAsync(true); // user clicked Delete

        await vm.DeleteCommand.ExecuteAsync(Item());

        api.Verify(a => a.DeleteConversationAsync("c1", It.IsAny<CancellationToken>()), Times.Once);
    }
}
