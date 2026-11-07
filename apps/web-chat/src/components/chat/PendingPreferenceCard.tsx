"use client";

// PendingPreferenceCard — S8 surface for one suggested memory (an inferred
// preference or, since FIX130, a fact the user stated about themselves).
// Lives at the top of the Memory Settings page inside the suggestions
// section; renders one card per pending_confirmation memory.
//
// FIX130 A3c — it says the same three things a saved memory does: what
// (content, category in the user's language), when (absolute date + the
// relative time) and from which chat (title as a link / deleted chat), plus
// the user's own words the suggestion was drawn from.
//
// Two actions:
//   Confirm → activate (calls api.confirmMemory(id, true))
//   Reject  → api.deleteMemory(id); chat-edge records a rejected
//             suggestion as status "rejected" so it is never suggested again
//
// The card intentionally uses the primary blue CTA on Confirm
// (matches the plan-card primary buttons from Phase 4) so it
// reads as the recommended action; Reject is a muted secondary.

import { Check, X } from "lucide-react";

import { MemoryOrigin, MemoryWhen } from "@/components/chat/MemoryCard";
import { useT } from "@/lib/i18n";
import { memoryCategoryLabel } from "@/lib/memoryLabels";
import type { Memory } from "@/lib/types";

interface Props {
	memory: Memory;
	onConfirm: (id: string) => void | Promise<void>;
	onReject: (id: string) => void | Promise<void>;
	saving?: boolean;
	/** Fallback title lookup for a server that does not resolve it (see MemoryCard). */
	resolveConversationTitle?: (id: string) => string | undefined;
}

export function PendingPreferenceCard({ memory, onConfirm, onReject, saving, resolveConversationTitle }: Props) {
	const t = useT();
	const metaParts: string[] = [];
	const category = memoryCategoryLabel(memory.category, t);
	if (category) metaParts.push(category);
	metaParts.push(t("memory.card.source.assistantInferred"));

	return (
		<div className="rounded-lg border border-border bg-bg-surface px-4 py-3">
			<p className="text-sm text-text-primary" data-testid="memory-content">{memory.content}</p>
			<p className="mt-1 text-xs text-text-muted" data-testid="memory-meta">
				{metaParts.join(" · ")}
			</p>
			<MemoryWhen memory={memory} labelKey="memory.card.suggestedOn" />
			<MemoryOrigin memory={memory} resolveConversationTitle={resolveConversationTitle} />

			{memory.evidence_excerpt ? (
				<p className="mt-1 text-xs italic text-text-muted">
					&ldquo;{memory.evidence_excerpt}&rdquo;
				</p>
			) : null}

			<div className="mt-3 flex items-center gap-2">
				<button
					type="button"
					onClick={() => void onConfirm(memory.id)}
					disabled={saving}
					className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-60"
				>
					<Check className="h-3.5 w-3.5" aria-hidden />
					{t("memory.pending.confirm")}
				</button>
				<button
					type="button"
					onClick={() => void onReject(memory.id)}
					disabled={saving}
					className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:opacity-60"
				>
					<X className="h-3.5 w-3.5" aria-hidden />
					{t("memory.pending.reject")}
				</button>
			</div>
		</div>
	);
}
