"use client";

// Chat thread.
//
// Two rendering modes — plain map for short threads, virtualized
// list (via @tanstack/react-virtual) once the count crosses
// VIRTUALIZE_THRESHOLD. Both modes share the same scroll container,
// the same auto-scroll behaviour, and the same "↓ New messages" /
// scroll-to-bottom pills.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import type { Message } from "@/lib/types";
import { useT } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import {
  ANCHOR_PREVIOUS_PEEK_PX,
  anchorTarget,
  shouldResumeFollow,
  spacerNeeded,
} from "@/lib/turnAnchor";
import { EmptyState } from "./EmptyState";
import { MessageBubble } from "./MessageBubble";
import { toolActivityLabelKey } from "./ToolActivityIndicator";

interface Props {
  convId: string;
  messages: Message[];
  onRegenerate: (messageId: string) => void;
  onPromptPick?: (text: string) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onSwitchBranch?: (messageId: string, branchIndex: number) => void;
  /** FIX33 B5 — quote-reply: selected assistant text → the composer. */
  onQuote?: (quoted: string) => void;
  /** Older-page lazy-load. ChatThread renders a "Load earlier" pill
   *  at the top of the scroll container; clicking calls the
   *  parent's loader. After older messages are prepended the
   *  scroll position is preserved automatically. */
  hasMoreOlder?: boolean;
  loadingOlder?: boolean;
  onLoadEarlier?: () => Promise<void> | void;
}

const NEAR_BOTTOM_PX = 100;
const SHOW_SCROLL_BUTTON_PX = 200;
const VIRTUALIZE_THRESHOLD = 50;
/** Frames the anchor jump takes (≈250 ms at 60 Hz). */
const ANCHOR_SCROLL_FRAMES = 15;
/** Keys that scroll the thread when focus is inside it. */
const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);

/** The key a row renders under — stable across the stream's temp→persisted
 *  id swap (see Message.client_key). */
const rowKey = (m: Message) => m.client_key ?? m.id;

export function ChatThread({
  convId,
  messages,
  onRegenerate,
  onPromptPick,
  onQuote,
  onEditMessage,
  onSwitchBranch,
  hasMoreOlder,
  loadingOlder,
  onLoadEarlier,
}: Props) {
  const t = useT();
  // T2 — stalled-stream notice: the idle watchdog in chatStore flags a
  // conversation whose active stream has received no bytes (events or
  // server heartbeats) past the threshold; the retry rides the
  // existing resume machinery.
  // Two independent signals, one notice: the byte-based watchdog catches
  // a dead CONNECTION (nothing arrives at all), while chat-edge's
  // provider_stall event catches a live connection whose MODEL has gone
  // quiet — heartbeats keep the byte clock fresh, so only the server can
  // see that one.
  const byteStalled = useChatStore((s) => Boolean(s.stalledStreams[convId]));
  const providerStalled = useChatStore((s) =>
    Boolean(s.providerStalledStreams[convId]),
  );
  const stalled = byteStalled || providerStalled;
  const retryStalledStream = useChatStore((s) => s.retryStalledStream);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [autoStick, setAutoStick] = useState(true);
  const [farFromBottom, setFarFromBottom] = useState(false);
  const [pendingNew, setPendingNew] = useState(false);
  const lastLen = useRef(0);
  const lastLastContent = useRef("");
  const autoScrolling = useRef(false);
  // FIX30 §1: structural growth of the last row — tool cards, plan, code
  // runs, a consent card — is "content" for the scroller too. Until now only
  // a text delta or a new row woke the auto-scroll, so a tool-only turn
  // (plan → sub-agent cards → consent) grew below the fold with the view
  // still parked on the user's message and no pill: the consent card was in
  // the DOM, off-screen under the composer, and the turn waited on a decision
  // nobody could see.
  const lastStructure = useRef("");
  const lastConsentId = useRef<string | null>(null);
  const [consentPill, setConsentPill] = useState(false);

  // ── P3 — send-time turn anchoring ────────────────────────────
  // On the user's own send, the new turn scrolls near the viewport top
  // (ANCHOR_PREVIOUS_PEEK_PX of the previous message peeking above it)
  // and a dynamic bottom spacer makes that reachable in short threads.
  // `active` is the hold phase: while true, streaming does NOT drag the
  // view; once the reply fills the viewport (shouldResumeFollow) the
  // pre-existing stick-to-bottom takes over. Any real user scroll
  // cancels the hold (see onScroll).
  const anchorActive = useRef(false);
  /** Where the hold wants the view: the anchor target of the current
   *  turn. The hold's spacer + handover math read THIS, not the live
   *  scrollTop — while the anchor jump is still animating the live value
   *  is somewhere on the way there, and sizing the spacer from it made
   *  the target unreachable. */
  const anchorTop = useRef(0);
  const lastUserMsgKey = useRef<string | null>(null);
  const spacerRef = useRef<HTMLDivElement>(null);
  /** The programmatic scroll animation in flight (rAF handle), if any. */
  const scrollAnim = useRef<number | null>(null);
  /** Set by the input events that mean "the person is scrolling" (wheel,
   *  touch, keys, scrollbar). A scroll event that arrives while one of
   *  our own scrolls is in flight is ours unless this says otherwise. */
  const userIntent = useRef(false);
  const lastScrollTop = useRef(0);

  const spacerPx = () =>
    spacerRef.current ? parseFloat(spacerRef.current.style.height) || 0 : 0;
  const setSpacerPx = (px: number) => {
    // Imperative on purpose: sizing the spacer must land in the same
    // layout pass as the scrollTop write, and a React state round-trip
    // per streamed token would be wasted renders.
    if (spacerRef.current) spacerRef.current.style.height = `${Math.round(px)}px`;
  };

  /** Content offset of a row's top (getBoundingClientRect so the
   *  virtualizer's translateY positioning is included). */
  const rowTopOf = (row: HTMLElement, el: HTMLElement) =>
    row.getBoundingClientRect().top -
    el.getBoundingClientRect().top +
    el.scrollTop;

  /** Scroll the sent message to the anchor position and size the spacer
   *  so the position is reachable. `tolerance` lets the late-measure
   *  re-pass skip when the first pass already landed close enough.
   *  In a virtualized thread the appended row may not be realized yet
   *  (user was scrolled far up) — jump to the tail first so the
   *  virtualizer renders it, then retry for a few frames. */
  function applyAnchor(userIdx: number, tolerance = 0, retries = 5) {
    const el = scrollerRef.current;
    if (!el) return false;
    const row = el.querySelector<HTMLElement>(`[data-msg-index="${userIdx}"]`);
    if (!row) {
      if (retries <= 0) return false;
      autoScrolling.current = true;
      el.scrollTop = el.scrollHeight;
      requestAnimationFrame(() => {
        autoScrolling.current = false;
        if (anchorActive.current) applyAnchor(userIdx, 8, retries - 1);
      });
      return true;
    }
    const prevRow = el.querySelector<HTMLElement>(
      `[data-msg-index="${userIdx - 1}"]`,
    );
    const target = anchorTarget(
      rowTopOf(row, el),
      prevRow ? rowTopOf(prevRow, el) : null,
      ANCHOR_PREVIOUS_PEEK_PX,
    );
    // The spacer is re-derived on every pass: the composer shrinks back
    // in the frame after the send, so the viewport measured on the first
    // pass is a line or two short and the target sits just out of reach
    // until this re-pass sizes the room for the real viewport.
    const contentH = el.scrollHeight - spacerPx();
    setSpacerPx(spacerNeeded(target, el.clientHeight, contentH));
    // Already there, or a jump is already on its way there (the
    // late-measure re-pass lands mid-jump) → no new animation.
    const heading = scrollAnim.current !== null ? anchorTop.current : el.scrollTop;
    anchorTop.current = target;
    if (Math.abs(heading - target) <= tolerance) return true;
    // The anchor jump ANIMATES: it is a deliberate move the user asked for
    // by sending, and a hard cut loses the sense that the thread scrolled.
    // Only this jump animates; the per-token stick-to-bottom during
    // streaming stays instant (an animation restarted on every token
    // fights itself).
    animateScroll(el, target);
    return true;
  }

  /** Programmatic scroll that animates unless the OS asks for reduced
   *  motion. Driven from requestAnimationFrame, one scrollTop write per
   *  frame, so every scroll event it raises happens while
   *  `autoScrolling` is set and onScroll can tell it from the person's
   *  own scrolling. (The previous driver, CSS scroll-behavior:smooth,
   *  kept firing scroll events for ~200 ms after the flag was cleared;
   *  the first of those counted as a user scroll and cancelled the hold
   *  it had just set up — measured on every send, web and desktop.) */
  function animateScroll(el: HTMLElement, top: number) {
    cancelScrollAnimation();
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const from = el.scrollTop;
    if (reduced || Math.abs(top - from) < 2) {
      snapTo(el, top);
      return;
    }
    autoScrolling.current = true;
    let frame = 0;
    const step = () => {
      frame += 1;
      const p = Math.min(1, frame / ANCHOR_SCROLL_FRAMES);
      const eased = 1 - Math.pow(1 - p, 3);
      el.scrollTop = from + (top - from) * eased;
      lastScrollTop.current = el.scrollTop;
      if (p < 1) {
        scrollAnim.current = requestAnimationFrame(step);
        return;
      }
      // One more frame so the last write's scroll event is dispatched
      // (and ignored) before the flag drops.
      scrollAnim.current = requestAnimationFrame(() => {
        scrollAnim.current = null;
        autoScrolling.current = false;
      });
    };
    scrollAnim.current = requestAnimationFrame(step);
  }

  function cancelScrollAnimation() {
    if (scrollAnim.current !== null) {
      cancelAnimationFrame(scrollAnim.current);
      scrollAnim.current = null;
    }
    autoScrolling.current = false;
  }

  /** Instant programmatic scroll. The flag covers the scroll event the
   *  write raises (dispatched before the next frame's callbacks). */
  function snapTo(el: HTMLElement, top: number) {
    cancelScrollAnimation();
    autoScrolling.current = true;
    el.scrollTop = top;
    lastScrollTop.current = el.scrollTop;
    requestAnimationFrame(() => {
      autoScrolling.current = false;
    });
  }

  // Opening or switching to a conversation ALWAYS lands at the bottom —
  // the newest turn, the way Claude/ChatGPT/Gemini behave. This replaced a
  // per-conversation scroll memory that restored the previous reading
  // position: switching away and back dropped you mid-thread with no sign
  // that newer messages existed below. "Continue the conversation" is the
  // intent behind opening one; a stale offset is never what you meant.
  useLayoutEffect(() => {
    // P3 — a turn anchor never crosses conversations.
    anchorActive.current = false;
    lastUserMsgKey.current = null;
    cancelScrollAnimation();
    if (spacerRef.current) spacerRef.current.style.height = "0px";
    // Follow state must reset too: carrying autoStick=false over from a
    // conversation the user had scrolled up in left the new one parked
    // wherever it happened to render, with only a pill for company.
    setAutoStick(true);
    setPendingNew(false);
    setFarFromBottom(false);
    const scroller = scrollerRef.current;
    if (scroller) {
      autoScrolling.current = true;
      scroller.scrollTop = scroller.scrollHeight;
    }
    // Force the message effect below to treat this as a fresh render so
    // the bottom-anchoring actually fires once the list has rendered.
    lastLen.current = -1;
    lastLastContent.current = "\u0000";
  }, [convId]);

  // Track the top message id + scroll offset BEFORE every render so
  // a prepend (older-page load) can restore the user's viewport.
  const prevFirstId = useRef<string | null>(null);
  const preservedTopHeight = useRef<number | null>(null);

  // useLayoutEffect runs synchronously after DOM mutations but
  // BEFORE the browser paints — that's the only place where a
  // scrollTop adjustment will be invisible to the user.
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const newFirstId = messages[0]?.id ?? null;
    const isPrepend =
      preservedTopHeight.current != null &&
      newFirstId !== null &&
      newFirstId !== prevFirstId.current;
    if (isPrepend) {
      const delta = el.scrollHeight - preservedTopHeight.current!;
      el.scrollTop = el.scrollTop + delta;
    }
    prevFirstId.current = newFirstId;
    preservedTopHeight.current = null;
  });

  // Auto-scroll to bottom when:
  //   - a new message arrives (length changed)
  //   - the last message's content grew (streaming)
  // …unless the user has scrolled up (autoStick=false), in which
  // case we mark `pendingNew` so a "↓ New messages" pill appears.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    const prevLen = lastLen.current;
    const lengthGrew = messages.length !== prevLen;
    const contentGrew = last?.content !== lastLastContent.current;
    const structure = structureKeyOf(last);
    const structureGrew = structure !== lastStructure.current;
    if (!lengthGrew && !contentGrew && !structureGrew) return;
    lastLen.current = messages.length;
    lastLastContent.current = last?.content ?? "";
    lastStructure.current = structure;

    // P3 — detect the user's OWN send: the last user message carries a
    // new key AND the list grew during this conversation's lifetime.
    // A remote/incoming message never satisfies this (no new local user
    // row), a load-earlier prepend keeps the last user key, the stream's
    // temp→persisted id swap keeps it too (rowKey), and the special
    // second arm covers the very first turn of a just-created
    // conversation, where the thread mounts already mid-stream with
    // nothing to restore.
    const userIdx = lastUserIndex(messages);
    const userMsg = userIdx >= 0 ? messages[userIdx] : null;
    const seenUserKey = lastUserMsgKey.current;
    lastUserMsgKey.current = userMsg ? rowKey(userMsg) : null;
    const isOwnSend =
      userMsg !== null &&
      rowKey(userMsg) !== seenUserKey &&
      ((lengthGrew && prevLen > 0) ||
        // Second arm: the very first turn of a just-created conversation,
        // where the thread mounts already mid-stream. Bounded to that one
        // case (the user message plus its streaming reply) so switching
        // INTO an existing conversation that happens to be streaming still
        // lands at the bottom, per rule 3.
        (prevLen === -1 && Boolean(last?.isStreaming) && messages.length <= 2));

    // P3 — anchor the user's own turn near the top, unconditionally:
    // it fires pinned-to-bottom, scrolled-up, or position-restored
    // alike (deliberately overriding "the reading position wins" for
    // one's own send only).
    if (isOwnSend) {
      anchorActive.current = true;
      if (applyAnchor(userIdx)) {
        setAutoStick(false); // follow is suspended while the hold lasts
        setPendingNew(false); // one's own send never raises the pill
        // Late-measure re-pass: virtualized rows start at the 200px
        // estimate; re-derive the target next frame once real
        // measurements land so the anchor sits where it should.
        requestAnimationFrame(() => {
          if (anchorActive.current) applyAnchor(userIdx, 8);
        });
        return;
      }
      anchorActive.current = false; // row unreachable — old behaviour
    }

    // FIX30 §1: a consent card is the one thing in a turn the USER must act
    // on, so it must never sit where the user cannot see it. Following, or
    // still holding the send anchor → drop the hold and bring it into view
    // (the card sits at the tail of the streaming row). Scrolled up on
    // purpose → the user always wins: raise the "Permission needed" pill
    // instead; the waiting notice above the composer offers "Show" either way.
    const consentId = last?.pending_consent?.promptId ?? null;
    const consentArrived = consentId !== null && consentId !== lastConsentId.current;
    lastConsentId.current = consentId;
    if (consentId === null && consentPill) setConsentPill(false);
    if (consentArrived) {
      if (anchorActive.current || autoStick) {
        anchorActive.current = false;
        setSpacerPx(0);
        setAutoStick(true);
        snapTo(el, el.scrollHeight);
      } else {
        setConsentPill(true);
      }
      return;
    }

    if (anchorActive.current) {
      // ── Phase 1: HOLD. The reply streams into the space below the
      // anchor; the view does not move. The spacer is re-derived from
      // the anchor target and the real content every time, so it
      // shrinks to zero exactly as the reply fills the room (and stays
      // exact if the streaming placeholder shrinks for a moment).
      const contentH = el.scrollHeight - spacerPx();
      const top = anchorTop.current;
      setSpacerPx(spacerNeeded(top, el.clientHeight, contentH));
      // ── Phase 2 handover: content reached the viewport bottom →
      // drop the hold and let the pre-existing stick-to-bottom follow
      // from here (shouldResumeFollow is THE policy line to tune).
      if (shouldResumeFollow(contentH, top, el.clientHeight)) {
        anchorActive.current = false;
        setAutoStick(true); // the next delta snaps to the bottom
      }
      return;
    }

    if (autoStick) {
      snapTo(el, el.scrollHeight);
    } else {
      setPendingNew(true);
    }
  }, [messages, autoStick, consentPill]);

  // After streaming completes: if user is still "near bottom" (within
  // 100px) snap to bottom to show the full response.
  const anyStreaming = messages.some((m) => m.isStreaming);

  // ── Screen-reader announcements ─────────────────────────────
  // One persistent polite live region for the whole thread. Persistent
  // (always mounted) because a region inserted together with its content
  // is unreliably announced by NVDA/JAWS — this is also why CatIndicator
  // is visual-only. Announces only on STATE EDGES (phase change /
  // completion), never per streamed token: the announcement text derives
  // from the phase key, which is stable across content_delta renders.
  const streamingMsg = messages.find((m) => m.isStreaming);
  // A generic tool in flight (tool_activity "started" without its
  // "finished") takes precedence over the thinking label — the phase
  // token doubles as the change-detection key, so a tool switch
  // announces once and streamed tokens never re-trigger it.
  const runningTool = streamingMsg?.tool_activities?.find(
    (a) => a.phase === "started",
  );
  // FIX30 §3.2: what the turn is waiting on, if anything — the consent card
  // (store state, exact) or a long local tool (the host's heartbeat frames).
  const waitingConsent = streamingMsg?.pending_consent ?? null;
  const toolWait = streamingMsg?.agent_wait?.kind === "tool" ? streamingMsg.agent_wait : null;
  const phaseKey: string | null = streamingMsg
    ? waitingConsent
      ? `consent:${waitingConsent.tool}`
      : runningTool
        ? `tool:${runningTool.tool}`
        : streamingMsg.is_compacting
          ? "gen.compacting"
          : streamingMsg.is_searching
            ? "gen.searching"
            : "gen.working"
    : null;
  const [announcement, setAnnouncement] = useState("");
  const prevPhaseRef = useRef<string | null>(null);
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    if (phaseKey) {
      wasStreamingRef.current = true;
      if (prevPhaseRef.current !== phaseKey) {
        prevPhaseRef.current = phaseKey;
        if (phaseKey.startsWith("consent:")) {
          const sub = waitingConsent?.requestedBy?.kind === "subagent" ? waitingConsent.requestedBy : null;
          setAnnouncement(sub
            ? t("thread.waitingConsentSub", { tool: phaseKey.slice("consent:".length), label: sub.label || t("consent.subagentUnnamed") })
            : t("thread.waitingConsent", { tool: phaseKey.slice("consent:".length) }));
        } else if (phaseKey.startsWith("tool:")) {
          const tool = phaseKey.slice("tool:".length);
          const key = toolActivityLabelKey(tool);
          setAnnouncement(key ? t(key) : t("tool.generic", { tool }));
        } else {
          setAnnouncement(t(phaseKey as "gen.searching" | "gen.working"));
        }
      }
    } else if (wasStreamingRef.current) {
      // true → false edge: the turn finished.
      wasStreamingRef.current = false;
      prevPhaseRef.current = null;
      setAnnouncement(t("a11y.responseComplete"));
    }
  }, [phaseKey, t]);
  useEffect(() => {
    if (anyStreaming) return;
    const el = scrollerRef.current;
    if (!el) return;
    // P3 — the turn ended while still anchored (short reply): keep the
    // view exactly where it is, drop the hold, and trim the spacer to
    // the minimum the current position needs. Never bottom-snap an
    // anchored turn.
    if (anchorActive.current) {
      anchorActive.current = false;
      const contentH = el.scrollHeight - spacerPx();
      setSpacerPx(spacerNeeded(el.scrollTop, el.clientHeight, contentH));
      return;
    }
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distance < NEAR_BOTTOM_PX) snapTo(el, el.scrollHeight);
  }, [anyStreaming]);

  /** The input events that mean the PERSON is scrolling. A scroll event
   *  raised by one of our own writes never follows one of these; a
   *  wheel/touch/key during the anchor animation cancels the animation
   *  so the person's scroll wins immediately. */
  function markUserIntent() {
    userIntent.current = true;
    if (scrollAnim.current !== null) cancelScrollAnimation();
    window.setTimeout(() => {
      userIntent.current = false;
    }, 250);
  }

  function onScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    const prevTop = lastScrollTop.current;
    lastScrollTop.current = el.scrollTop;
    if (autoScrolling.current && !userIntent.current) return;
    userIntent.current = false;
    // P3 — the user always wins: any real scroll cancels the anchor
    // hold for this turn (the autoStick logic below then decides
    // whether to follow).
    anchorActive.current = false;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distance < NEAR_BOTTOM_PX;
    // Scrolling UP while a reply streams stops the follow at once, however
    // small the move — the person is reading, the way Claude/ChatGPT stop.
    // A scroll that lands ON the bottom is not that (the scroller clamps
    // there by itself when content above shrinks).
    const scrolledUp = el.scrollTop < prevTop - 1 && distance > 1;
    setAutoStick(nearBottom && !(scrolledUp && anyStreaming));
    setFarFromBottom(distance > SHOW_SCROLL_BUTTON_PX);
    if (nearBottom && !scrolledUp) {
      setPendingNew(false);
      setConsentPill(false);
    }
    // A finished anchored turn keeps the spacer that held the view in
    // place; once the person moves, keep only what the NEW position needs,
    // so a short answer's blank room below goes away as they scroll off it.
    if (!anyStreaming && spacerPx() > 0) {
      const contentH = el.scrollHeight - spacerPx();
      const needed = spacerNeeded(el.scrollTop, el.clientHeight, contentH);
      if (needed < spacerPx()) setSpacerPx(needed);
    }
  }

  function jumpToBottom() {
    const el = scrollerRef.current;
    if (!el) return;
    setAutoStick(true);
    setPendingNew(false);
    animateScroll(el, el.scrollHeight);
  }

  /** FIX30 §1: bring the consent card into view (centre it); with no
   *  scrollIntoView (jsdom) fall back to the tail, where the card lives. */
  function jumpToConsent() {
    const el = scrollerRef.current;
    if (!el) return;
    setConsentPill(false);
    const card = el.querySelector<HTMLElement>('[data-testid="consent-card"]');
    if (card && typeof card.scrollIntoView === "function") {
      anchorActive.current = false;
      setAutoStick(true);
      cancelScrollAnimation();
      autoScrolling.current = true;
      card.scrollIntoView({ block: "center" });
      window.setTimeout(() => {
        autoScrolling.current = false;
      }, 400);
      return;
    }
    jumpToBottom();
  }

  async function handleLoadEarlier() {
    if (!onLoadEarlier || loadingOlder || !hasMoreOlder) return;
    const el = scrollerRef.current;
    if (el) preservedTopHeight.current = el.scrollHeight;
    try {
      await onLoadEarlier();
    } catch {
      preservedTopHeight.current = null;
    }
  }

  const lastAsstIdx = useMemo(() => lastAssistantIndex(messages), [messages]);

  const shouldVirtualize = messages.length >= VIRTUALIZE_THRESHOLD;

  return (
    <div className="relative flex-1 overflow-hidden">
      {/* SR-only live region — the single announcement source for the
          thread (start/phase-change/finish). Never wraps the streaming
          markdown body: per-token announcements would flood the reader. */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
      <div
        ref={scrollerRef}
        onScroll={onScroll}
        onWheel={markUserIntent}
        onTouchMove={markUserIntent}
        onKeyDown={(e) => {
          if (SCROLL_KEYS.has(e.key)) markUserIntent();
        }}
        onPointerDown={(e) => {
          // the scrollbar is the only part of the scroller itself one can press
          if (e.target === e.currentTarget) markUserIntent();
        }}
        className="scrollbar-thin h-full overflow-y-auto px-4 py-6 md:px-10"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {messages.length === 0 ? (
            <EmptyState onPrompt={(t) => onPromptPick?.(t)} />
          ) : (
            <>
              <LoadEarlierBar
                visible={!!hasMoreOlder}
                loading={!!loadingOlder}
                onClick={handleLoadEarlier}
              />
              {shouldVirtualize ? (
                <VirtualMessageList
                  scrollerRef={scrollerRef}
                  convId={convId}
                  messages={messages}
                  lastAsstIdx={lastAsstIdx}
                  anyStreaming={anyStreaming}
                  onRegenerate={onRegenerate}
                  onEditMessage={onEditMessage}
                  onSwitchBranch={onSwitchBranch}
                  onQuote={onQuote}
                />
              ) : (
                <PlainMessageList
                  convId={convId}
                  messages={messages}
                  lastAsstIdx={lastAsstIdx}
                  anyStreaming={anyStreaming}
                  onRegenerate={onRegenerate}
                  onEditMessage={onEditMessage}
                  onSwitchBranch={onSwitchBranch}
                  onQuote={onQuote}
                />
              )}
            </>
          )}
        </div>
        {/* P3 — turn-anchor spacer. Pure geometry, never content: it is
            aria-hidden, carries no text (copy/export read the store),
            sits OUTSIDE the virtualizer's container so row indices and
            measurements are untouched, and its height is driven
            imperatively by the anchor logic (0 outside an anchored
            turn, shrinking to 0 as the reply streams in). */}
        <div
          ref={spacerRef}
          aria-hidden="true"
          data-testid="turn-spacer"
          style={{ height: 0 }}
        />
      </div>

      {waitingConsent ? (
        // FIX30 §3.2: say what the turn is waiting on, where the user is
        // looking (above the composer, like the stalled notice), with a way
        // to reach the card. Wins over "stalled": a turn waiting for the
        // user is not stalled, whatever the byte clock says.
        <div
          role="status"
          data-testid="waiting-consent-notice"
          className="popover-appear absolute bottom-16 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-primary shadow-lg backdrop-blur-sm"
        >
          {waitingConsent.requestedBy?.kind === "subagent"
            ? t("thread.waitingConsentSub", { tool: waitingConsent.tool, label: waitingConsent.requestedBy.label || t("consent.subagentUnnamed") })
            : t("thread.waitingConsent", { tool: waitingConsent.tool })}
          <button
            type="button"
            onClick={jumpToConsent}
            className="focus-ring rounded-full bg-text-primary px-2.5 py-0.5 font-medium text-bg-base hover:opacity-90"
          >
            {t("thread.showConsent")}
          </button>
        </div>
      ) : toolWait ? (
        <div
          role="status"
          data-testid="tool-running-notice"
          className="popover-appear absolute bottom-16 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-secondary shadow-lg backdrop-blur-sm"
        >
          {t("thread.toolRunning", { tool: toolWait.tool, s: String(Math.round(toolWait.elapsedMs / 1000)) })}
        </div>
      ) : stalled ? (
        <div
          role="status"
          data-testid="stalled-stream-notice"
          className="popover-appear absolute bottom-16 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-secondary shadow-lg backdrop-blur-sm"
        >
          {t("thread.stalled")}
          <button
            type="button"
            onClick={() => retryStalledStream(convId)}
            className="focus-ring rounded-full bg-text-primary px-2.5 py-0.5 font-medium text-bg-base hover:opacity-90"
          >
            {t("common.retry")}
          </button>
        </div>
      ) : null}

      {consentPill && waitingConsent ? (
        <button
          type="button"
          data-testid="consent-pill"
          onClick={jumpToConsent}
          className="focus-ring popover-appear absolute bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-text-primary px-3 py-1.5 text-xs font-medium text-bg-base shadow-xl hover:opacity-90"
        >
          <svg width={13} height={13} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 5v14M5 12l7 7 7-7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {t("thread.permissionNeeded")}
        </button>
      ) : pendingNew && !autoStick ? (
        <button
          type="button"
          onClick={jumpToBottom}
          className="focus-ring popover-appear absolute bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-text-primary px-3 py-1.5 text-xs font-medium text-bg-base shadow-xl hover:opacity-90"
        >
          <svg width={13} height={13} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 5v14M5 12l7 7 7-7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {t("thread.newMessages")}
        </button>
      ) : farFromBottom ? (
        <button
          type="button"
          aria-label={t("thread.scrollToLatest")}
          onClick={jumpToBottom}
          className="focus-ring popover-appear absolute bottom-6 left-1/2 -translate-x-1/2 rounded-full border border-border bg-bg-surface p-2 text-text-secondary shadow-lg backdrop-blur-sm hover:text-text-primary"
        >
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M6 9l6 6 6-6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      ) : null}
    </div>
  );
}

/** FIX30 §1: the parts of a row that grow WITHOUT a text delta. */
function structureKeyOf(m: Message | undefined): string {
  if (!m) return "";
  const acts = m.tool_activities ?? [];
  let finished = 0;
  for (const a of acts) if (a.phase === "finished") finished++;
  return `${acts.length}/${finished}|${m.plan?.length ?? 0}|${m.code_runs?.length ?? 0}|${m.pending_consent?.promptId ?? ""}`;
}

// ─── Inner list renderers ──────────────────────────────────────

interface InnerProps {
  convId: string;
  messages: Message[];
  lastAsstIdx: number;
  anyStreaming: boolean;
  onRegenerate: (messageId: string) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onSwitchBranch?: (messageId: string, branchIndex: number) => void;
  /** FIX33 B5 — quote-reply: selected assistant text → the composer. */
  onQuote?: (quoted: string) => void;
}

function PlainMessageList(props: InnerProps) {
  return (
    <>
      {props.messages.map((m, i) => renderRow(m, i, props))}
    </>
  );
}

function VirtualMessageList(
  props: InnerProps & {
    scrollerRef: React.RefObject<HTMLDivElement>;
  },
) {
  const { scrollerRef, messages } = props;
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollerRef.current,
    estimateSize: () => 200,
    overscan: 5,
    measureElement(element) {
      // Some browsers report 0 during a transition; the virtualizer
      // tolerates it but a measured pixel value is preferable.
      return element?.getBoundingClientRect().height ?? 0;
    },
  });

  const items = virtualizer.getVirtualItems();
  return (
    <div
      style={{
        position: "relative",
        height: virtualizer.getTotalSize(),
        width: "100%",
      }}
    >
      {items.map((vi) => {
        const m = messages[vi.index];
        return (
          <div
            key={rowKey(m)}
            data-index={vi.index}
            ref={virtualizer.measureElement}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${vi.start}px)`,
            }}
          >
            {renderRow(m, vi.index, props)}
          </div>
        );
      })}
    </div>
  );
}

function renderRow(m: Message, i: number, props: InnerProps) {
  const {
    convId,
    messages,
    lastAsstIdx,
    anyStreaming,
    onRegenerate,
    onEditMessage,
    onSwitchBranch,
    onQuote,
  } = props;
  const prev = messages[i - 1];
  const extraGap = prev && prev.role !== m.role ? "mt-2" : "";
  return (
    // data-msg-index — the P3 anchor locates rows by index in BOTH
    // render modes (getBoundingClientRect, so the virtualizer's
    // translateY is included). Keyed by rowKey so the id swap after
    // message_start never re-creates the bubble.
    <div key={rowKey(m)} className={extraGap} data-msg-index={i}>
      <MessageBubble
        convId={convId}
        message={m}
        onQuote={m.role === "assistant" && onQuote ? onQuote : undefined}
        showFeedback={
          m.role === "assistant" &&
          !m.isStreaming &&
          m.status === "completed"
        }
        showRegenerate={
          m.role === "assistant" &&
          i === lastAsstIdx &&
          !anyStreaming &&
          m.status !== "regenerated"
        }
        onRegenerate={() => onRegenerate(m.id)}
        onEdit={
          m.role === "user" && onEditMessage && !anyStreaming
            ? (text) => onEditMessage(m.id, text)
            : undefined
        }
        onSwitchBranch={
          onSwitchBranch ? (idx) => onSwitchBranch(m.id, idx) : undefined
        }
      />
    </div>
  );
}

function LoadEarlierBar({
  visible,
  loading,
  onClick,
}: {
  visible: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  const t = useT();
  if (!visible) return null;
  return (
    <div className="-mt-2 flex justify-center pb-2">
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        className="focus-ring inline-flex items-center gap-1.5 rounded-full border border-border bg-bg-secondary/70 px-3 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-wait disabled:opacity-60"
      >
        {loading ? (
          <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
        ) : (
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M6 15l6-6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
        {t("thread.loadEarlier")}
      </button>
    </div>
  );
}

function lastAssistantIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return i;
  }
  return -1;
}

function lastUserIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return i;
  }
  return -1;
}
