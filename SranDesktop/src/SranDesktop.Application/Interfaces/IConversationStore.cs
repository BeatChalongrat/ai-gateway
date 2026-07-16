using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Interfaces;

/// <summary>Local SQLite persistence for conversations, messages, files and tool executions.</summary>
public interface IConversationStore
{
    Task InitializeAsync(CancellationToken ct = default);

    // Conversations
    Task<IReadOnlyList<Conversation>> GetConversationsAsync(CancellationToken ct = default);
    Task<Conversation?> GetConversationAsync(string id, CancellationToken ct = default);
    Task UpsertConversationAsync(Conversation conversation, CancellationToken ct = default);
    Task DeleteConversationAsync(string id, CancellationToken ct = default);

    // Messages
    Task<IReadOnlyList<Message>> GetMessagesAsync(string conversationId, CancellationToken ct = default);
    Task UpsertMessageAsync(Message message, CancellationToken ct = default);
    /// <summary>Upserts all rows in ONE transaction — a crash mid-turn can
    /// never persist a user message without its assistant reply.</summary>
    Task UpsertMessagesAsync(IReadOnlyList<Message> messages, CancellationToken ct = default);
    Task<int> GetNextSortOrderAsync(string conversationId, CancellationToken ct = default);

    // Push-sync state (Phase 3 D). messages.server_id stores the SERVER
    // conversation id as a synced marker; conversations.server_id maps the
    // local conversation to its chat-edge mirror.
    Task SetConversationServerIdAsync(string conversationId, string serverId, CancellationToken ct = default);
    Task<IReadOnlyList<Message>> GetUnsyncedMessagesAsync(string conversationId, CancellationToken ct = default);
    Task MarkMessagesSyncedAsync(IReadOnlyList<string> messageIds, string serverConversationId, CancellationToken ct = default);

    // Composer drafts (Phase 3 C4). Empty text deletes the row.
    Task SaveDraftAsync(string conversationId, string text, CancellationToken ct = default);
    Task<string?> GetDraftAsync(string conversationId, CancellationToken ct = default);

    // Files
    Task<IReadOnlyList<FileAttachment>> GetFilesAsync(string conversationId, CancellationToken ct = default);
    Task UpsertFileAsync(FileAttachment file, CancellationToken ct = default);

    // Tool executions (Agent Mode, Phase 2)
    Task InsertToolExecutionAsync(ToolExecution execution, CancellationToken ct = default);
    Task<IReadOnlyList<ToolExecution>> GetToolExecutionsAsync(string conversationId, CancellationToken ct = default);

    // Tool permissions (Agent Mode, Phase 2)
    Task<string?> GetToolPermissionAsync(string toolName, string scope, CancellationToken ct = default);
    Task SetToolPermissionAsync(string toolName, string scope, string permission, CancellationToken ct = default);

    // Branch sets (R10 Fix 3): one row per fork point — ordered message-id
    // lists per tail + the active tail index, so ◀ n/m ▶ survives restarts.
    Task SaveBranchSetAsync(BranchSetRecord record, CancellationToken ct = default);
    Task<IReadOnlyList<BranchSetRecord>> GetBranchSetsAsync(string conversationId, CancellationToken ct = default);

    // Agent memory notes (Phase B PART 3): short durable model-authored hints
    // scoped to a conversation. AddNoteAsync keeps only the newest N per
    // conversation (older ones are pruned); GetNotesAsync returns them oldest
    // first for the ## Notes system-prompt block.
    Task AddNoteAsync(string conversationId, string content, CancellationToken ct = default);
    Task<IReadOnlyList<Domain.Entities.AgentNote>> GetNotesAsync(string conversationId, CancellationToken ct = default);
}
