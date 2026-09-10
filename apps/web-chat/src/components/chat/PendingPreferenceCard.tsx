"use client";

// PendingPreferenceCard — S8 surface for one inferred-preference
// suggestion. Lives at the top of the Memory Settings page
// inside a "Suggested Preferences" section; renders one card per
// pending_confirmation memory.
//
// Two actions:
//   Confirm → activate (calls api.confirmMemory(id, true))
//   Reject  → soft-delete (calls api.deleteMemory(id))
//
// The card intentionally uses the primary blue CTA on Confirm
// (matches the plan-card primary buttons from Phase 4) so it
// reads as the recommended action; Reject is a muted secondary.

import { Check, X } from "lucide-react";

import { timeAgo } from "@/components/workspace/workspaceUtil";
import { useT } from "@/lib/i18n";
import type { Memory } from "@/lib/types";

interface Props {
	memory: Memory;
	onConfirm: (id: string) => void | Promise<void>;
	onReject: (id: string) => void | Promise<void>;
	saving?: boolean;
}

export function PendingPreferenceCard({ memory, onConfirm, onReject, saving }: Props) {
	const t = useT();
	const metaParts: string[] = [];
	if (memory.category) metaParts.push(memory.category);
	metaParts.push(t("memory.type.inferred"));
	if (memory.source_conversation_id) {
		metaParts.push(
			t("memory.card.source.fromConvAgo", { time: timeAgo(memory.created_at) }),
		);
	} else {
		metaParts.push(
			t("memory.card.source.detectedAgo", { time: timeAgo(memory.created_at) }),
		);
	}

	return (
		<div className="rounded-lg border border-border bg-bg-surface px-4 py-3">
			<p className="text-sm text-text-primary">{memory.content}</p>
			<p className="mt-1 text-xs text-text-muted">{metaParts.join(" · ")}</p>

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
