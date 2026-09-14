// FIX111 — the server is the one summariser.
//
// chat-edge owns the transcript (FIX 9, FIX109), so it owns the summary of
// it too. Until FIX111 the desktop summarised for itself: GatewayStateExtractor
// asked the model directly and kept the result under <userData>/
// conversation-state as an original nobody else could read. A conversation
// compacted here and continued on the web was summarised again from zero on
// the server — two summaries of one conversation, paid for twice, neither
// aware of the other. FIX109 opened the read half (the desktop adopts the
// server's state when it builds from the server transcript); this is the
// write half, through the door that already existed: POST /compact, which
// chat-edge's own "summarise and continue" uses (FIX33 A-W7).
//
// What that door does — read before changing the caller: it folds every
// visible row the server holds into chat.conversation_states (batches of 120,
// at most 8 per call, the conversation's model or CONTEXT_SUMMARY_MODEL,
// billed to the compaction ledger) and then sets the manual-compact boundary
// at the newest row. So a desktop compaction is a FULL compaction on both
// surfaces: the next turn, here and on the web, sends the first exchange, the
// summary pair and what came after the boundary. The desktop's old fold
// covered only the middle it was about to drop; the server has no such
// entry point, and the same boundary on both surfaces is the point.
//
// The state comes back anchored by row id. The local chain has no ids, so
// the caller translates through the server's visible rows, fetched here in
// the same order and with the same filter the transcript loader uses
// (conversation-state.ts anchorStateOnChain).
//
// No 'electron' import: unit-tested in plain Node with a fake client.

import type { ChatEdgeClient, CompactionTrigger, MessageDto } from "./chat-edge-client";
import type { Logger } from "./logger";

/** The transcript loader's ceiling (turn-coordinator.ts HISTORY_LOAD_CEILING). */
const ROW_CEILING = 1000;
const PAGE = 100;

export interface ServerCompaction {
  /** The state row as chat-edge returned it — GET /state's shape. */
  state: Record<string, unknown>;
  /** The row the server set the boundary at (its newest visible row). */
  boundaryMessageId: string | null;
  /** Whether the summary reached that row (a partial fold stops earlier). */
  coverageReached: boolean;
  /** Ids of the server's visible rows, oldest first: the frame the id
   *  anchors are placed in. */
  rowIds: string[];
  /** True when the rows stopped at the ceiling — older rows exist unseen. */
  rowsTruncated: boolean;
}

export interface ConversationSummariser {
  /** Ask the owner of the transcript to summarise everything it holds.
   *  Null when it could not (unreachable, refused, no summary produced) —
   *  the turn then goes out trimmed with the honest note, as it does
   *  without a summariser at all. Never throws. */
  compact(conversationId: string, instructions: string, trigger: CompactionTrigger, signal: AbortSignal): Promise<ServerCompaction | null>;
}

/** Rows the model would see on the server: user/assistant with text. */
export function isVisibleRow(m: MessageDto): boolean {
  return (m.role === "user" || m.role === "assistant") && typeof m.content === "string" && m.content.trim().length > 0;
}

export class ChatEdgeSummariser implements ConversationSummariser {
  constructor(
    private readonly chatEdge: Pick<ChatEdgeClient, "compactConversation" | "listMessagesPage">,
    private readonly log: Logger,
  ) {}

  async compact(conversationId: string, instructions: string, trigger: CompactionTrigger, signal: AbortSignal): Promise<ServerCompaction | null> {
    if (signal.aborted) return null;
    const startedAt = Date.now();
    const res = await this.chatEdge.compactConversation(conversationId, instructions, trigger);
    if (!res || !res.state || typeof res.state !== "object") {
      this.log.warn(`SUMMARY conv=${conversationId}: server compact returned nothing after ${Date.now() - startedAt} ms — no summary this turn`);
      return null;
    }
    const rows = await this.visibleRowIds(conversationId);
    if (!rows) {
      this.log.warn(`SUMMARY conv=${conversationId}: server compacted but its rows could not be read — the summary cannot be placed, not adopted`);
      return null;
    }
    const boundary = typeof res.boundary_message_id === "string" && res.boundary_message_id ? res.boundary_message_id : null;
    this.log.info(`SUMMARY conv=${conversationId}: server compact done in ${Date.now() - startedAt} ms — boundary ${boundary ? rows.ids.indexOf(boundary) + 1 : "?"} of ${rows.ids.length} rows, coverage ${res.coverage_reached === true ? "reached" : "partial"}`);
    return { state: res.state as Record<string, unknown>, boundaryMessageId: boundary, coverageReached: res.coverage_reached === true, rowIds: rows.ids, rowsTruncated: rows.truncated };
  }

  /** The server's visible rows, oldest first, paged back like the
   *  transcript loader. Null when a page cannot be read. */
  private async visibleRowIds(conversationId: string): Promise<{ ids: string[]; truncated: boolean } | null> {
    const pages: MessageDto[][] = [];
    let before: string | undefined;
    let hasMore = true;
    let total = 0;
    while (hasMore && total < ROW_CEILING) {
      const page = await this.chatEdge.listMessagesPage(conversationId, PAGE, before);
      if (!page) return null;
      pages.unshift(page.messages);
      total += page.messages.length;
      hasMore = page.hasMore && page.messages.length > 0;
      before = page.messages[0]?.id;
    }
    return { ids: pages.flat().filter(isVisibleRow).map((m) => m.id), truncated: hasMore };
  }
}
