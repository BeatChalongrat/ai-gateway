namespace SranDesktop.Domain.Enums;

public enum ConversationSource
{
    /// <summary>Conversation lives on chat-edge-api (Chat Mode).</summary>
    Server,

    /// <summary>Conversation lives only in local SQLite (Agent Mode).</summary>
    Local
}
