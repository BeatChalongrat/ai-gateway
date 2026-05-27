// Inline AI avatar shown on the left of every assistant message.
// Picks a per-provider mark so OpenAI / Anthropic / Google etc. are
// visually distinct. Falls back to a generic sparkle for unknown
// providers.

interface Props {
  modelId?: string | null;
  size?: number;
  /** When true, render the provider mark with a neutral surface
   *  instead of the per-provider tinted ring. Used by the chat-input
   *  model pill, where a saturated coloured chip reads as noise. The
   *  full colour scheme stays on message-bubble avatars. */
  neutral?: boolean;
}

type Provider = "openai" | "anthropic" | "google" | "meta" | "mistral" | "generic";

function providerOf(modelId?: string | null): Provider {
  if (!modelId) return "generic";
  const id = modelId.toLowerCase();
  if (id.includes("gpt") || id.includes("openai") || id.startsWith("o1") || id.startsWith("o3") || id.startsWith("o4")) {
    return "openai";
  }
  if (id.includes("claude") || id.includes("anthropic") || id.includes("sonnet") || id.includes("opus") || id.includes("haiku")) {
    return "anthropic";
  }
  if (id.includes("gemini") || id.includes("google") || id.includes("bard") || id.includes("palm")) {
    return "google";
  }
  if (id.includes("llama") || id.includes("meta")) {
    return "meta";
  }
  if (id.includes("mistral") || id.includes("mixtral")) {
    return "mistral";
  }
  return "generic";
}

export function AssistantAvatar({ modelId, size = 26, neutral = false }: Props) {
  const provider = providerOf(modelId);
  const ringClass = neutral
    ? "bg-transparent text-text-secondary"
    : providerRing(provider);

  return (
    <span
      style={{ width: size, height: size }}
      className={`inline-flex shrink-0 items-center justify-center rounded-full ${ringClass}`}
      aria-label={`${provider} model`}
      title={modelId ?? "AI"}
    >
      <ProviderMark provider={provider} size={Math.round(size * 0.6)} />
    </span>
  );
}

function providerRing(p: Provider): string {
  switch (p) {
    case "openai":
      return "bg-[#0d201a] text-emerald-300 ring-1 ring-emerald-700/50";
    case "anthropic":
      return "bg-[#2a1d12] text-orange-300 ring-1 ring-orange-700/50";
    case "google":
      return "bg-[#0e1830] text-sky-300 ring-1 ring-sky-700/50";
    case "meta":
      return "bg-[#0d1a2e] text-blue-300 ring-1 ring-blue-700/50";
    case "mistral":
      return "bg-[#2b1810] text-amber-300 ring-1 ring-amber-700/50";
    default:
      return "bg-gradient-to-br from-accent to-accent-subtle text-white";
  }
}

function ProviderMark({ provider, size }: { provider: Provider; size: number }) {
  switch (provider) {
    case "openai":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M22.28 9.82a5.99 5.99 0 0 0-.51-4.92 6.06 6.06 0 0 0-6.53-2.9A6 6 0 0 0 4.98 4.18a5.99 5.99 0 0 0-4 2.9 6.06 6.06 0 0 0 .74 7.1 6 6 0 0 0 .51 4.93 6.06 6.06 0 0 0 6.53 2.9 6 6 0 0 0 10.26-1.18 5.99 5.99 0 0 0 4-2.9 6.06 6.06 0 0 0-.74-7.11Zm-9.06 12.66a4.5 4.5 0 0 1-2.89-1.05l.14-.08 4.78-2.76a.78.78 0 0 0 .39-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.59a4.5 4.5 0 0 1-4.48 4.5Zm-9.64-4.12a4.5 4.5 0 0 1-.54-3.03l.14.08 4.78 2.76a.78.78 0 0 0 .78 0l5.84-3.37v2.33a.07.07 0 0 1-.03.06l-4.83 2.79a4.5 4.5 0 0 1-6.14-1.62Zm-1.26-10.4a4.5 4.5 0 0 1 2.34-1.98v5.69a.78.78 0 0 0 .39.67l5.83 3.37-2.02 1.16a.07.07 0 0 1-.07 0L3.96 14.07a4.5 4.5 0 0 1-1.64-6.11Zm16.66 3.87L13.16 8.45l2.02-1.16a.07.07 0 0 1 .07 0l4.83 2.79a4.5 4.5 0 0 1-.69 8.12v-5.69a.79.79 0 0 0-.4-.68Zm2.01-3.02-.14-.09-4.78-2.76a.78.78 0 0 0-.78 0L8.45 9.33V7a.07.07 0 0 1 .03-.06l4.83-2.78a4.5 4.5 0 0 1 6.68 4.65ZM7.36 13.35l-2.03-1.17a.07.07 0 0 1-.04-.05V6.55a4.5 4.5 0 0 1 7.37-3.46l-.13.08-4.78 2.76a.78.78 0 0 0-.4.68Zm1.1-2.37 2.6-1.5 2.6 1.5v3l-2.6 1.5-2.6-1.5Z" />
        </svg>
      );
    case "anthropic":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M14.55 3h-2.78L17 21h2.78L14.55 3ZM8.34 3 3 21h2.83l1.09-3.78h5.62L13.62 21h2.83L11.11 3H8.34Zm-.58 11.5L9.7 7.91l1.94 6.59H7.76Z" />
        </svg>
      );
    case "google":
      // The Gemini "spark" — four asymmetric petals around a centre.
      // Closer to the official Gemini mark than the equal-arm star
      // we shipped before.
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M12 2c.4 3.2 1.5 5.7 3.3 7.5C17.1 11.3 19.6 12.4 22.8 12.8c-3.2.4-5.7 1.5-7.5 3.3-1.8 1.8-2.9 4.3-3.3 7.5-.4-3.2-1.5-5.7-3.3-7.5C6.9 14.3 4.4 13.2 1.2 12.8c3.2-.4 5.7-1.5 7.5-3.3C10.5 7.7 11.6 5.2 12 2Z" />
        </svg>
      );
    case "meta":
      // Lemniscate / infinity mark — Meta's brand uses the same
      // ribbon shape; one continuous Bezier path replaces the
      // hand-drawn wavy fill we had before.
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M3 12c0-3.3 2-6 4.5-6 2.2 0 3.7 2 4.5 4 .8-2 2.3-4 4.5-4 2.5 0 4.5 2.7 4.5 6s-2 6-4.5 6c-2.2 0-3.7-2-4.5-4-.8 2-2.3 4-4.5 4-2.5 0-4.5-2.7-4.5-6Z"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "mistral":
      // Stylised "M" with two strokes — replaces the
      // placeholder-looking grid-of-blocks we had before.
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M4 19V5l8 8 8-8v14"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    default:
      // Single clean six-point sparkle for unknown / generic
      // providers. Was a double-star before — too busy at 16px.
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M12 3c.3 3 1.2 5.2 2.6 6.6C16 11 18.2 11.9 21.2 12.2c-3 .3-5.2 1.2-6.6 2.6C13.2 16.2 12.3 18.4 12 21.4c-.3-3-1.2-5.2-2.6-6.6C8 13.4 5.8 12.5 2.8 12.2c3-.3 5.2-1.2 6.6-2.6C10.8 8.2 11.7 6 12 3Z" />
        </svg>
      );
  }
}
