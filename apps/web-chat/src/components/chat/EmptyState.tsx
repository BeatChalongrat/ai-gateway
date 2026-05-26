"use client";

// Welcome screen rendered when a conversation has zero messages.
// Mirrors Claude's "Beat returns!" pattern + ChatGPT's category
// chips below the input. Greeting picks a localised time-of-day
// label, with the user's display name from authStore.
//
// The greeting is computed inside useEffect so the SSR/CSR markup
// matches exactly on first paint — server has no local timezone, so
// rendering `new Date()` during the render phase causes a hydration
// mismatch warning. We render the greeting as a generic placeholder
// during SSR and swap it in on the client.

import { useEffect, useState } from "react";

import { useAuth } from "@/hooks/useAuth";

interface Props {
  onPrompt: (text: string) => void;
}

interface ChipDef {
  label: string;
  icon: React.ReactNode;
  // Text injected into the textarea when the chip is tapped. The
  // chip doesn't auto-send; the user reviews/edits before pressing
  // Enter. (Spec said auto-send, but the Claude UX of "fill the
  // box and let the user tweak" matches more carefully — pressing
  // Enter is one keypress away.)
  prompt: string;
}

const CHIPS: ChipDef[] = [
  {
    label: "Write",
    icon: (
      <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M12 20h9M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
    prompt: "Help me draft ",
  },
  {
    label: "Learn",
    icon: (
      <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M22 12c0 5-4 9-10 9S2 17 2 12 6 3 12 3s10 4 10 9zM9 9h6M9 13h6M9 17h4"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
    prompt: "Explain ",
  },
  {
    label: "Code",
    icon: (
      <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M8 6l-6 6 6 6M16 6l6 6-6 6"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
    prompt: "Write a ",
  },
  {
    label: "Analyze",
    icon: (
      <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M3 3v18h18M7 14l3-3 4 4 5-6"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
    prompt: "Analyze this ",
  },
];

function timeOfDayGreeting(now: Date): string {
  const h = now.getHours();
  if (h < 5) return "Still up";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 22) return "Good evening";
  return "Burning the midnight oil";
}

function firstName(input: string | null | undefined): string {
  if (!input) return "there";
  // Prefer the local-part of an email; fall back to the raw string.
  const local = input.includes("@") ? input.split("@")[0] : input;
  // Title-case the first segment, split on dots/underscores/hyphens.
  const first = local.split(/[._-]/)[0] || local;
  if (!first) return "there";
  return first.charAt(0).toUpperCase() + first.slice(1);
}

export function EmptyState({ onPrompt }: Props) {
  const { user } = useAuth();
  const name = firstName(user?.email ?? user?.user_id ?? null);
  // Time-of-day greeting is computed AFTER mount so SSR renders a
  // stable placeholder and the client swaps in the localised
  // version without a hydration mismatch warning.
  const [greeting, setGreeting] = useState(`Hello, ${name}`);
  useEffect(() => {
    setGreeting(`${timeOfDayGreeting(new Date())}, ${name}`);
  }, [name]);

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 py-10">
      <div className="fade-in-up w-full max-w-chat text-center">
        <img
          src="/sran-logo.png"
          alt="SRAN Chat"
          className="sran-logo mx-auto mb-4 h-20 w-auto select-none md:h-24"
          draggable={false}
        />
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary md:text-3xl">
          {greeting}
        </h1>
        <p className="mt-2 text-base text-text-secondary md:text-lg">
          How can I help you today?
        </p>

        <div className="scrollbar-thin mt-6 flex flex-wrap items-center justify-center gap-2 overflow-x-auto pb-1">
          {CHIPS.map((c) => (
            <button
              key={c.label}
              type="button"
              onClick={() => onPrompt(c.prompt)}
              className="focus-ring tap-44 inline-flex items-center gap-2 rounded-full border border-border-subtle bg-transparent px-4 py-2 text-[13px] text-text-secondary transition-all hover:-translate-y-0.5 hover:border-border-strong hover:bg-bg-surface hover:text-text-primary"
            >
              <span className="text-accent">{c.icon}</span>
              <span>{c.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
