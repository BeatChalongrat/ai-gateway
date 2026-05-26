// Inline AI avatar shown on the left of every assistant message.
// Picks a per-provider mark so OpenAI / Anthropic / Google etc. are
// visually distinct. Falls back to a generic sparkle for unknown
// providers.

interface Props {
  modelId?: string | null;
  size?: number;
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

export function AssistantAvatar({ modelId, size = 26 }: Props) {
  const provider = providerOf(modelId);
  const ringClass = providerRing(provider);

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
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M12 2 9.6 9.6 2 12l7.6 2.4L12 22l2.4-7.6L22 12l-7.6-2.4Z" />
        </svg>
      );
    case "meta":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M2 12c0-4 2-7 5-7 2.6 0 4.4 2.4 5.3 5 0.9-2.6 2.7-5 5.3-5 3 0 5 3 5 7 0 1.5-.3 3-1 4-.6 1-1.5 1.6-2.7 1.6-2.2 0-3.3-2-4.6-5-1.3-3-2-4-3.3-4s-2 1-3.3 4c-1.3 3-2.4 5-4.6 5C2.7 17.6 2 14.5 2 12Z" />
        </svg>
      );
    case "mistral":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M3 4h4v4H3V4Zm14 0h4v4h-4V4ZM3 10h4v4H3v-4Zm7 0h4v4h-4v-4Zm7 0h4v4h-4v-4ZM3 16h4v4H3v-4Zm14 0h4v4h-4v-4Z" />
        </svg>
      );
    default:
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Zm6 9 .9 2.5 2.5.9-2.5.9-.9 2.5-.9-2.5-2.5-.9 2.5-.9.9-2.5Z" />
        </svg>
      );
  }
}
