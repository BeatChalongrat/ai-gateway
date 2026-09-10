"use client";

// SourcesCard — S7 attribution surface for assistant messages.
//
// Renders the "Sources used" rail under each assistant turn that
// drew on saved memory / conversation state / past-conversation
// search / compaction. Collapsed by default so the chat thread
// stays clean; expanded shows one row per source with type-
// specific actions on hover (edit memory, open past conversation,
// hide).
//
// Returns null when sources is empty — the message bubble doesn't
// have to defensive-check upstream.

import { useMemo, useState } from "react";

import {
	Archive,
	Brain,
	ChevronDown,
	ChevronUp,
	ExternalLink,
	FileText,
	Layers,
	MessageSquare,
	Pencil,
	X,
} from "lucide-react";

import { useT } from "@/lib/i18n";
import type { ContextSource, ContextSourceType } from "@/lib/types";

interface Props {
	sources: ContextSource[];
	onEditMemory?: (memoryId: string | null | undefined) => void;
	onOpenConversation?: (conversationId: string | null | undefined) => void;
}

// Monochrome icons by type — same text-text-muted treatment the
// other Phase 4 / S2 cards use. The icon SHAPE alone carries the
// type hint (no per-type colour wash).
const SOURCE_ICONS: Record<ContextSourceType, JSX.Element> = {
	saved_memory: <Brain className="h-3.5 w-3.5 text-text-muted" />,
	conversation_state: <Layers className="h-3.5 w-3.5 text-text-muted" />,
	past_conversation: <MessageSquare className="h-3.5 w-3.5 text-text-muted" />,
	file_context: <FileText className="h-3.5 w-3.5 text-text-muted" />,
	compacted_history: <Archive className="h-3.5 w-3.5 text-text-muted" />,
};

const SOURCE_LABELS: Record<ContextSourceType, string> = {
	saved_memory: "saved memory",
	conversation_state: "conversation state",
	past_conversation: "past conversation",
	file_context: "file context",
	compacted_history: "compacted history",
};

export function SourcesCard({ sources, onEditMemory, onOpenConversation }: Props) {
	const [expanded, setExpanded] = useState(false);
	// Local hide list — "Not relevant" dismissal is client-only
	// today (no backend feedback path yet). Hidden ids stay
	// hidden for the lifetime of the component instance.
	const [hidden, setHidden] = useState<Set<string>>(() => new Set());

	const visible = useMemo(
		() => sources.filter((s) => !hidden.has(s.id)),
		[sources, hidden],
	);

	if (!visible || visible.length === 0) return null;

	return (
		<div className="mt-1.5 overflow-hidden rounded-lg border border-border-subtle bg-bg-surface">
			<button
				type="button"
				onClick={() => setExpanded((v) => !v)}
				className="focus-ring flex w-full items-center justify-between px-3 py-1.5 text-xs text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-secondary"
				aria-expanded={expanded}
			>
				<span>
					Sources ({visible.length})
				</span>
				{expanded ? (
					<ChevronUp className="h-3.5 w-3.5" aria-hidden />
				) : (
					<ChevronDown className="h-3.5 w-3.5" aria-hidden />
				)}
			</button>

			{expanded ? (
				<div className="space-y-1 border-t border-border-subtle px-3 py-2">
					{visible.map((source) => (
						<SourceRow
							key={source.id}
							source={source}
							onEdit={
								source.source_type === "saved_memory"
									? () => onEditMemory?.(source.source_id)
									: undefined
							}
							onOpen={
								source.source_type === "past_conversation"
									? () => onOpenConversation?.(source.source_id)
									: undefined
							}
							onHide={() =>
								setHidden((prev) => {
									const next = new Set(prev);
									next.add(source.id);
									return next;
								})
							}
						/>
					))}
				</div>
			) : null}
		</div>
	);
}

function SourceRow({
	source,
	onEdit,
	onOpen,
	onHide,
}: {
	source: ContextSource;
	onEdit?: () => void;
	onOpen?: () => void;
	onHide: () => void;
}) {
	const t = useT();
	const icon = SOURCE_ICONS[source.source_type] ?? (
		<FileText className="h-3.5 w-3.5 text-text-muted" />
	);
	const typeLabel = SOURCE_LABELS[source.source_type] ?? source.source_type;
	const meta = secondaryLine(source);
	const label = localizedLabel(source, t);

	return (
		<div className="group flex items-start gap-2 py-1.5">
			<span className="mt-0.5 flex-shrink-0">{icon}</span>
			<div className="min-w-0 flex-1">
				<p className="truncate text-xs text-text-secondary" title={label}>
					{label}
				</p>
				<p className="text-[11px] text-text-muted">
					{typeLabel}
					{meta ? ` · ${meta}` : null}
				</p>
			</div>
			<div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
				{onEdit ? (
					<RowAction label="Edit memory" onClick={onEdit}>
						<Pencil className="h-3 w-3" aria-hidden />
					</RowAction>
				) : null}
				{onOpen ? (
					<RowAction label="Open conversation" onClick={onOpen}>
						<ExternalLink className="h-3 w-3" aria-hidden />
					</RowAction>
				) : null}
				<RowAction label="Not relevant" onClick={onHide}>
					<X className="h-3 w-3" aria-hidden />
				</RowAction>
			</div>
		</div>
	);
}

function RowAction({
	label,
	onClick,
	children,
}: {
	label: string;
	onClick: () => void;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			aria-label={label}
			title={label}
			onClick={onClick}
			className="focus-ring rounded p-1 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary"
		>
			{children}
		</button>
	);
}

// localizedLabel (FIX102 Part E) — the compaction row was built as an
// English sentence in Go, so a Thai screen read "Conversation compacted (22
// older messages summarized)". chat-edge now sends a code plus the numbers
// in metadata, the way error codes reach localizeErrorCode; the sentence is
// written here. Rows persisted before the code existed keep the label they
// were stored with.
export function localizedLabel(source: ContextSource, t: ReturnType<typeof useT>): string {
	const meta = source.metadata ?? {};
	if (source.source_type !== "compacted_history" || meta["code"] !== "conversation_compacted" || typeof meta["missing"] !== "number") {
		return source.display_label;
	}
	const n = String(meta["missing"]);
	switch (meta["summary"]) {
		case "failed":
			return t("sources.compacted.noSummary", { n });
		case "partial":
			return t("sources.compacted.partial", { n });
		case "off":
			return t("sources.compacted.memoryOff", { n });
		default:
			return t("sources.compacted", { n });
	}
}

// secondaryLine renders the muted metadata tail after the type
// label — category for memories, conversation title + date for
// past-conversation hits. Returns "" so the JSX caller can branch
// cleanly.
function secondaryLine(source: ContextSource): string {
	const meta = source.metadata ?? {};
	const bits: string[] = [];
	switch (source.source_type) {
		case "saved_memory": {
			const category = meta["category"];
			if (typeof category === "string" && category) bits.push(category);
			break;
		}
		case "past_conversation": {
			const title = meta["conversation_title"];
			const date = meta["date"];
			if (typeof title === "string" && title) bits.push(title);
			if (typeof date === "string" && date) bits.push(date);
			break;
		}
		case "file_context": {
			const filename = meta["filename"];
			if (typeof filename === "string" && filename) bits.push(filename);
			break;
		}
	}
	return bits.join(" · ");
}
