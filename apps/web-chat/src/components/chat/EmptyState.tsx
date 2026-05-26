"use client";

interface Props {
  onPrompt: (text: string) => void;
}

const PROMPTS = [
  "Explain quantum computing in simple terms",
  "Write a Python function to sort a list",
  "Help me draft a professional email",
  "What are the benefits of exercise?",
];

export function EmptyState({ onPrompt }: Props) {
  return (
    <div className="flex flex-1 items-center justify-center px-4 py-8">
      <div className="fade-in-up w-full max-w-2xl text-center">
        <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-accent to-accent-subtle shadow-2xl">
          <svg width={28} height={28} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 2 9.6 9.6 2 12l7.6 2.4L12 22l2.4-7.6L22 12l-7.6-2.4Z"
              fill="white"
              opacity="0.95"
            />
          </svg>
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-text-primary md:text-2xl">
          How can I help you today?
        </h1>
        <p className="mt-2 text-sm text-text-muted">
          Ask anything, or pick a starter below.
        </p>
        <div className="mt-6 grid grid-cols-2 gap-2 md:grid-cols-2">
          {PROMPTS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => onPrompt(p)}
              className="focus-ring rounded-2xl border border-border bg-bg-secondary/40 px-4 py-3 text-left text-xs text-text-secondary transition-all hover:scale-[1.01] hover:border-accent/60 hover:bg-bg-elevated hover:text-text-primary md:text-sm"
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
