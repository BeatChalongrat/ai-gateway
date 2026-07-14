namespace SranDesktop.Application.Models;

/// <summary>One server-sent-event frame: optional event name + raw data payload.</summary>
public sealed record SseFrame(string? Event, string Data);
