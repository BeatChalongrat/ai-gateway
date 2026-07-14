namespace SranDesktop.Domain.ValueObjects;

public sealed record TokenUsage(int PromptTokens, int CompletionTokens, int TotalTokens)
{
    public static TokenUsage Zero { get; } = new(0, 0, 0);
}
