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
import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

interface Props {
  onPrompt: (text: string) => void;
  /** Optional slot rendered between the greeting and the chip row.
   *  The landing page passes its centered ChatInput here so the
   *  "empty" view becomes a single hero block (greeting → input →
   *  chips) vertically centred in the viewport. Conversation pages
   *  with zero messages can omit this and just show greeting +
   *  chips — their own input lives at the bottom of the page. */
  children?: React.ReactNode;
}

interface ChipDef {
  // Translation key (e.g. ``empty.chip.write``) — t() resolves it
  // at render time so a locale switch updates the chip text
  // without forcing the parent to re-render.
  labelKey: DictKey;
  promptKey: DictKey;
  icon: React.ReactNode;
}

const CHIPS: ChipDef[] = [
  {
    labelKey: "empty.chip.write",
    promptKey: "empty.chip.write.prompt",
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
  },
  {
    labelKey: "empty.chip.learn",
    promptKey: "empty.chip.learn.prompt",
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
  },
  {
    labelKey: "empty.chip.code",
    promptKey: "empty.chip.code.prompt",
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
  },
  {
    labelKey: "empty.chip.analyze",
    promptKey: "empty.chip.analyze.prompt",
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
  },
];

// Returns a translation-key for the active hour-of-day bucket; the
// caller runs it through ``t()`` so the greeting localises with the
// rest of the empty state.
function greetingKey(now: Date): DictKey {
  const h = now.getHours();
  if (h < 5) return "empty.greet.lateNight";
  if (h < 12) return "empty.greet.morning";
  if (h < 17) return "empty.greet.afternoon";
  if (h < 22) return "empty.greet.evening";
  return "empty.greet.midnight";
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

export function EmptyState({ onPrompt, children }: Props) {
  const { user } = useAuth();
  const t = useT();
  const name = firstName(user?.email ?? user?.user_id ?? null);
  // Time-of-day greeting key is computed AFTER mount so SSR renders
  // a stable placeholder and the client swaps in the localised
  // version without a hydration mismatch warning.
  const [greetKey, setGreetKey] = useState<DictKey | null>(null);
  useEffect(() => {
    setGreetKey(greetingKey(new Date()));
  }, []);
  // FIX34 Part E — the FIX33 B6 "Your skills" row of tool-name buttons is
  // gone: claude.ai keeps skills under Customize › Skills in the sidebar and
  // invokes them itself when relevant; ChatGPT pins GPTs in the sidebar. A
  // row of tools teaches "pick a tool, then ask" and becomes a wall at 20
  // skills. The Write / Learn / Code / Analyze chips stay — those are TASKS.
  // Skills are still applied automatically (chat-edge prompt_compiler layer
  // 10b; desktop skills._forPrompt) and reachable from the sidebar menu and
  // the "/" quick actions.
  const greeting = greetKey ? `${t(greetKey)}, ${name}` : `Hello, ${name}`;

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
          {t("empty.subtitle")}
        </p>

        {children ? <div className="mt-6 text-left">{children}</div> : null}

        <div className="scrollbar-thin mt-6 flex flex-wrap items-center justify-center gap-2 overflow-x-auto pb-1">
          {CHIPS.map((c) => (
            <button
              key={c.labelKey}
              type="button"
              onClick={() => onPrompt(t(c.promptKey))}
              className="focus-ring tap-44 inline-flex items-center gap-2 rounded-full border border-border-subtle bg-transparent px-4 py-2 text-[13px] text-text-secondary transition-all hover:-translate-y-0.5 hover:border-border-strong hover:bg-bg-surface hover:text-text-primary"
            >
              {/* Icon inherits the chip's text colour — no accent
                  tint, no green. The chips are intentionally muted
                  so they read as suggestions, not CTAs. */}
              <span className="text-text-tertiary">{c.icon}</span>
              <span>{t(c.labelKey)}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
