"use client";

// Chat thread.
//
// Two rendering modes — plain map for short threads, virtualized
// list (via @tanstack/react-virtual) once the count crosses
// VIRTUALIZE_THRESHOLD. Both modes share the same scroll container,
// the same auto-scroll behaviour, and the same "↓ New messages" /
// scroll-to-bottom pills.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { defaultRangeExtractor, elementScroll, useVirtualizer } from "@tanstack/react-virtual";

import type { Message } from "@/lib/types";
import { useT } from "@/lib/i18n";
import { knownNotRetryable } from "@/lib/errorMessages";
import { useChatStore } from "@/stores/chatStore";
import { useComposerStore } from "@/stores/composerStore";
import {
  ANCHOR_PREVIOUS_PEEK_PX,
  anchorTarget,
  landmarkCorrection,
  pickReadingLandmark,
  type RowBox,
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

const SCROLL_TOLERANCE_PX = 1;
const VIRTUALIZE_THRESHOLD = 50;
/** Frames the anchor jump takes (≈250 ms at 60 Hz). */
const ANCHOR_SCROLL_FRAMES = 15;
/** Keys that scroll the thread when focus is inside it. */
const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);

/** The key a row renders under — stable across the stream's temp→persisted
 *  id swap (see Message.client_key). */
const rowKey = (m: Message) => m.client_key ?? m.id;

/** The thread column's own bottom padding (py-6); FIX130 C4 raises it
 *  to the credits badge's reach when that is larger. */
export const THREAD_BOTTOM_PAD = 24;

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
  const badgeReach = useComposerStore((s) => s.badge); // FIX130 C4
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
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  // FIX122 Part A — the scroller as STATE as well as a ref. The virtualized
  // list is a child of the scroller div, and React attaches a host node's ref
  // AFTER its children's layout effects have run: the virtualizer, asking for
  // its scroll element in its own layout effect, got null, attached to
  // nothing and rendered no rows — a blank thread for every conversation of
  // VIRTUALIZE_THRESHOLD rows or more. (StrictMode's second effect pass finds
  // the ref set, so `next dev` never showed it; the production bundle always
  // did.) The first re-render — the person's first wheel tick — then attached
  // it, and attaching scrolls to the virtualizer's initial offset, 0: the
  // oldest message. Set from the ref callback, this state re-renders before
  // paint, and the list below is only mounted with the element in hand.
  const [scrollerEl, setScrollerEl] = useState<HTMLDivElement | null>(null);
  const attachScroller = useCallback((node: HTMLDivElement | null) => {
    scrollerRef.current = node;
    setScrollerEl(node);
  }, []);
  const [autoStick, setAutoStick] = useState(true);
  /** FIX88 Part D — the follow state as a ref, for the ResizeObserver
   *  below (its closure is created once; React state would be stale).
   *  Written wherever the state is, through setFollow. */
  const followRef = useRef(true);
  const setFollow = (v: boolean) => {
    followRef.current = v;
    setAutoStick(v);
  };
  const [farFromBottom, setFarFromBottom] = useState(false);
  const [pendingNew, setPendingNew] = useState(false);
  /** FIX127 D2 — a remembered scrollTop not yet applied (content still mounting). */
  const pendingRestore = useRef<number | null>(null);
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
  /** A live turn's temporary reservation. Terminal states always release it. */
  const holdTop = useRef<number | null>(null);
  const spacerRef = useRef<HTMLDivElement>(null);
  /** The programmatic scroll animation in flight: its rAF handle (for
   *  cancelling) and whether it is still running. */
  const scrollAnim = useRef<number | null>(null);
  const animating = useRef(false);
  /** Set by the input events that mean "the person is scrolling" (wheel,
   *  touch, keys, scrollbar). A scroll event that arrives while one of
   *  our own scrolls is in flight is ours unless this says otherwise. */
  const userIntent = useRef(false);
  const lastScrollTop = useRef(0);
  const turnEpoch = useRef(0);
  const scrollEpoch = useRef(0);
  const anchorUserKey = useRef<string | null>(null);
  const anchorReplyKey = useRef<string | null>(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const readingLandmark = useRef<{ key: string; offset: number } | null>(null);
  const intentEpoch = useRef(0);
  const lastTailKey = useRef<string | null>(null);
  const scrollRequest = useChatStore((s) => s.turnScrollRequests[convId]);

  function geometry(el: HTMLElement) {
    const column = el.firstElementChild as HTMLElement | null;
    const contentEnd = column
      ? column.getBoundingClientRect().bottom - el.getBoundingClientRect().top - el.clientTop + el.scrollTop
      : 0;
    const contentH = contentEnd + (parseFloat(getComputedStyle(el).paddingBottom) || 0);
    return { contentEnd, contentH, bottom: Math.max(0, contentH - el.clientHeight) };
  }

  function refreshAffordance(el: HTMLElement, newContent = false) {
    const below = geometry(el).contentEnd > el.scrollTop + el.clientHeight + SCROLL_TOLERANCE_PX;
    setFarFromBottom(below);
    setPendingNew((pending) => below && !followRef.current && !anchorActive.current && (pending || newContent));
    if (!below) setConsentPill(false);
  }

  function rememberReader(el: HTMLElement) {
    const edge = el.getBoundingClientRect().top + el.clientTop;
    const boxes: RowBox[] = [];
    for (const item of el.querySelectorAll<HTMLElement>("[data-msg-index]")) {
      const message = messagesRef.current[Number(item.dataset.msgIndex)];
      if (!message) continue;
      const box = item.getBoundingClientRect();
      boxes.push({ key: rowKey(message), top: box.top - edge, bottom: box.bottom - edge });
    }
    // DOM order is index order in both render modes (the virtualizer's
    // preserved row is sorted into place), which pickReadingLandmark needs.
    readingLandmark.current = pickReadingLandmark(boxes, el.clientHeight);
  }

  function releaseHold() {
    turnEpoch.current += 1;
    anchorActive.current = false;
    holdTop.current = null;
    anchorUserKey.current = null;
    anchorReplyKey.current = null;
    setSpacerPx(0);
  }

  const setSpacerPx = (px: number) => {
    // Imperative on purpose: sizing the spacer must land in the same
    // layout pass as the scrollTop write, and a React state round-trip
    // per streamed token would be wasted renders.
    if (spacerRef.current) spacerRef.current.style.height = `${Math.round(px)}px`;
  };

  /** FIX88 Part D — the hold ends and the follow begins, and the view goes
   *  to the bottom IN THE SAME PASS. It used to set the follow flag and
   *  wait for "the next delta" to snap; measured on the real bundle, the
   *  reply that crosses the viewport bottom only on its LAST flush (the
   *  RAF-batched buffer holding several lines, a burst before
   *  message_complete, a non-streaming answer landing at once) had no
   *  next delta — the hand-over happened, nothing followed, and the tail
   *  sat below the fold (435–535 px in the probe) with no pill, since a
   *  pill is for a reader who scrolled away and nobody had. */
  function handOver(el: HTMLElement) {
    releaseHold();
    setFollow(true);
    snapTo(el, geometry(el).bottom);
  }

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
  function applyAnchor(userIdx: number, tolerance = 0, retries = 5, epoch = turnEpoch.current) {
    const el = scrollerRef.current;
    if (!el || epoch !== turnEpoch.current || !anchorActive.current) return false;
    const row = el.querySelector<HTMLElement>(`[data-msg-index="${userIdx}"]`);
    if (!row) {
      if (retries <= 0) return false;
      snapTo(el, el.scrollHeight);
      requestAnimationFrame(() => {
        if (epoch !== turnEpoch.current) return;
        if (anchorActive.current) applyAnchor(userIdx, 8, retries - 1, epoch);
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
    const contentH = geometry(el).contentH;
    setSpacerPx(spacerNeeded(target, el.clientHeight, contentH));
    // Already there, or a jump is already on its way there (the
    // late-measure re-pass lands mid-jump) → no new animation.
    const heading = animating.current ? anchorTop.current : el.scrollTop;
    anchorTop.current = target;
    holdTop.current = target;
    refreshAffordance(el);
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
    const epoch = scrollEpoch.current;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const from = el.scrollTop;
    if (reduced || Math.abs(top - from) < 2) {
      snapTo(el, top);
      return;
    }
    autoScrolling.current = true;
    animating.current = true;
    let frame = 0;
    const step = () => {
      if (epoch !== scrollEpoch.current) return;
      frame += 1;
      const p = Math.min(1, frame / ANCHOR_SCROLL_FRAMES);
      const eased = 1 - Math.pow(1 - p, 3);
      el.scrollTop = from + (top - from) * eased;
      lastScrollTop.current = el.scrollTop;
      refreshAffordance(el);
      if (p < 1) {
        scrollAnim.current = requestAnimationFrame(step);
        return;
      }
      // One more frame so the last write's scroll event is dispatched
      // (and ignored) before the flag drops.
      scrollAnim.current = requestAnimationFrame(() => {
        if (epoch !== scrollEpoch.current) return;
        animating.current = false;
        autoScrolling.current = false;
        reconcileRef.current();
      });
    };
    scrollAnim.current = requestAnimationFrame(step);
  }

  function cancelScrollAnimation() {
    scrollEpoch.current += 1;
    if (scrollAnim.current !== null) {
      cancelAnimationFrame(scrollAnim.current);
      scrollAnim.current = null;
    }
    animating.current = false;
    autoScrolling.current = false;
  }

  /** Instant programmatic scroll. The flag covers the scroll event the
   *  write raises (dispatched before the next frame's callbacks). */
  function snapTo(el: HTMLElement, top: number) {
    cancelScrollAnimation();
    autoScrolling.current = true;
    el.scrollTop = top;
    lastScrollTop.current = el.scrollTop;
    refreshAffordance(el);
    const epoch = scrollEpoch.current;
    requestAnimationFrame(() => {
      if (epoch === scrollEpoch.current) autoScrolling.current = false;
    });
  }

  // Conversation and generation boundaries invalidate every queued anchor pass.
  useLayoutEffect(() => {
    releaseHold();
    cancelScrollAnimation();
    setPendingNew(false);
    setConsentPill(false);
    readingLandmark.current = null;
    lastConsentId.current = null;
    lastLen.current = -1;
    lastLastContent.current = "\u0000";
    const el = scrollerRef.current;
    // FIX127 D2 — a chat the person had scrolled away from the bottom of
    // opens where they left it (this session); every other chat opens at
    // the bottom, as before.
    const remembered = scrollMemory.get(convId);
    if (remembered && remembered.far) {
      setFollow(false);
      setFarFromBottom(true);
      // The rows mount one commit after the scroller (attachScroller), so
      // the position is applied by reconcile() once the content is there.
      pendingRestore.current = remembered.top;
      if (el) snapTo(el, remembered.top);
    } else {
      pendingRestore.current = null;
      setFollow(true);
      setFarFromBottom(false);
      if (el) snapTo(el, geometry(el).bottom);
    }
    return () => {
      turnEpoch.current += 1;
      cancelScrollAnimation();
    };
  }, [convId]);

  function reconcile(newContent = false) {
    const el = scrollerRef.current;
    if (!el) return;
    const currentMessages = messagesRef.current;
    if (anchorActive.current) {
      const reply = currentMessages.find((m) => rowKey(m) === anchorReplyKey.current);
      const live = reply && (reply.isStreaming || reply.status === "waiting_user" ||
        reply.pending_consent || reply.pending_question);
      // FIX91: terminal completion repays the entire reservation, including
      // short replies. A transport pause for a question is still a live turn.
      if (!live) {
        handOver(el);
        return;
      }
      const idx = currentMessages.findIndex((m) => rowKey(m) === anchorUserKey.current);
      const row = el.querySelector<HTMLElement>(`[data-msg-index="${idx}"]`);
      if (row) {
        const previous = el.querySelector<HTMLElement>(`[data-msg-index="${idx - 1}"]`);
        const target = anchorTarget(rowTopOf(row, el), previous ? rowTopOf(previous, el) : null, ANCHOR_PREVIOUS_PEEK_PX);
        if (Math.abs(target - anchorTop.current) > 1 && animating.current) animateScroll(el, target);
        anchorTop.current = target;
        holdTop.current = target;
        const contentH = geometry(el).contentH;
        setSpacerPx(spacerNeeded(target, el.clientHeight, contentH));
        if (!animating.current) {
          if (shouldResumeFollow(contentH, target, el.clientHeight)) {
            handOver(el);
            return;
          }
          if (Math.abs(el.scrollTop - target) > 1) snapTo(el, target);
        }
      }
    } else if (followRef.current) {
      setSpacerPx(0);
      const bottom = geometry(el).bottom;
      if (!animating.current && Math.abs(el.scrollTop - bottom) > 1) snapTo(el, bottom);
    } else {
      // FIX127 D2 — a remembered position waits for the content to be tall
      // enough, then is applied once; a chat that got shorter meanwhile
      // lands at its end.
      if (pendingRestore.current !== null) {
        const target = pendingRestore.current;
        const bottom = geometry(el).bottom;
        if (bottom >= target - 1) {
          pendingRestore.current = null;
          if (Math.abs(el.scrollTop - target) > 1) snapTo(el, target);
        } else if (currentMessages.length > 0 && bottom > 0 && Math.abs(el.scrollTop - bottom) > 1) {
          snapTo(el, bottom);
        }
        refreshAffordance(el, newContent);
        return;
      }
      const landmark = readingLandmark.current;
      if (landmark) {
        const idx = currentMessages.findIndex((m) => rowKey(m) === landmark.key);
        const row = el.querySelector<HTMLElement>(`[data-msg-index="${idx}"]`);
        if (idx >= 0 && !row) {
          // The virtualizer has not committed this row yet; do not replace
          // the saved reader with whichever estimated rows arrive first.
          refreshAffordance(el, newContent);
          return;
        }
        if (row) {
          const offset = row.getBoundingClientRect().top - el.getBoundingClientRect().top - el.clientTop;
          const corrected = landmarkCorrection(el.scrollTop, landmark.offset, offset, geometry(el).bottom);
          if (corrected !== null) snapTo(el, corrected);
        }
      }
      rememberReader(el);
    }
    refreshAffordance(el, newContent);
  }

  const reconcileRef = useRef<() => void>(() => {});
  reconcileRef.current = () => reconcile();

  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    const structure = structureKeyOf(last);
    const tailKey = last ? rowKey(last) : null;
    const newContent = (messages.length > lastLen.current && tailKey !== lastTailKey.current) || last?.content !== lastLastContent.current ||
      structure !== lastStructure.current;
    lastTailKey.current = tailKey;
    lastLen.current = messages.length;
    lastLastContent.current = last?.content ?? "";
    lastStructure.current = structure;

    if (scrollRequest) {
      const idx = messages.findIndex((m) => rowKey(m) === scrollRequest.userKey);
      if (scrollRequest.kind === "regenerate" || idx >= 0) {
        useChatStore.setState((state) => state.turnScrollRequests[convId]?.id === scrollRequest.id
          ? { turnScrollRequests: { ...state.turnScrollRequests, [convId]: undefined } }
          : state);
        const wasFollowing = followRef.current || anchorActive.current;
        releaseHold();
        cancelScrollAnimation();
        if (scrollRequest.kind === "send") {
          anchorActive.current = true;
          anchorUserKey.current = scrollRequest.userKey;
          anchorReplyKey.current = scrollRequest.replyKey;
          readingLandmark.current = null;
          setFollow(false);
          setPendingNew(false);
          setFarFromBottom(false);
          if (applyAnchor(idx)) {
            const epoch = turnEpoch.current;
            requestAnimationFrame(() => {
              if (epoch === turnEpoch.current && anchorActive.current) {
                const currentIdx = messagesRef.current.findIndex((m) => rowKey(m) === anchorUserKey.current);
                applyAnchor(currentIdx, 8, 5, epoch);
                reconcileRef.current();
              }
            });
          }
        } else {
          setFollow(wasFollowing);
        }
      }
    }

    const consentId = last?.pending_consent?.promptId ?? last?.pending_question?.promptId ?? null;
    const consentArrived = consentId !== null && consentId !== lastConsentId.current;
    lastConsentId.current = consentId;
    if (!consentId) setConsentPill(false);
    if (consentArrived) {
      if (anchorActive.current || followRef.current) handOver(el);
      else setConsentPill(geometry(el).contentEnd > el.scrollTop + el.clientHeight + 1);
    }
    reconcile(newContent);
    // scrollerEl: the virtualized list mounts one commit after the scroller
    // (see attachScroller) — that commit positions the view too.
  }, [messages, scrollRequest, convId, scrollerEl]);

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
  const waitingQuestion = streamingMsg?.pending_question ?? null;
  const toolWait = streamingMsg?.agent_wait?.kind === "tool" ? streamingMsg.agent_wait : null;
  const phaseKey: string | null = streamingMsg
    ? waitingConsent
      ? `consent:${waitingConsent.tool}`
      : waitingQuestion
        ? "question"
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
        if (phaseKey === "question") {
          setAnnouncement(t("thread.waitingQuestion"));
        } else if (phaseKey.startsWith("consent:")) {
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
    const el = scrollerRef.current;
    const column = el?.firstElementChild;
    if (!el || !column || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => reconcileRef.current());
    ro.observe(column);
    ro.observe(el);
    const restore = () => {
      if (document.visibilityState === "visible") reconcileRef.current();
    };
    document.addEventListener("visibilitychange", restore);
    return () => {
      ro.disconnect();
      document.removeEventListener("visibilitychange", restore);
    };
  }, []);

  function markUserIntent() {
    userIntent.current = true;
    const epoch = ++intentEpoch.current;
    requestAnimationFrame(() => {
      if (epoch === intentEpoch.current) userIntent.current = false;
    });
  }

  function onWheel(e: React.WheelEvent<HTMLDivElement>) {
    if (e.deltaY === 0) return;
    for (let node = e.target as HTMLElement; node && node !== e.currentTarget; node = node.parentElement!) {
      const max = node.scrollHeight - node.clientHeight;
      if (max > 1 && /auto|scroll/.test(getComputedStyle(node).overflowY) &&
          (e.deltaY > 0 ? node.scrollTop < max - 1 : node.scrollTop > 1)) return;
    }
    // A downward gesture at a browser-clamped bottom emits no scroll event.
    if (e.deltaY > 0 && !anchorActive.current && !followRef.current &&
        e.currentTarget.scrollTop >= geometry(e.currentTarget).bottom - 1) {
      jumpToBottom();
      return;
    }
    markUserIntent();
  }

  function onScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    const prevTop = lastScrollTop.current;
    const top = el.scrollTop;
    lastScrollTop.current = top;
    refreshAffordance(el);
    // FIX127 D2 — where this chat is being read, for the next visit.
    rememberScroll(convId, top, geometry(el).contentEnd > top + el.clientHeight + SCROLL_TOLERANCE_PX);
    if ((autoScrolling.current || animating.current) && !userIntent.current) return;
    // Native clamping after a shrink is geometry, not a request to stop reading.
    const clamped = Math.min(prevTop, Math.max(0, el.scrollHeight - el.clientHeight));
    if (!userIntent.current && Math.abs(top - clamped) <= 1) return;
    if (Math.abs(top - prevTop) <= 1) {
      userIntent.current = false;
      return;
    }
    userIntent.current = false;
    pendingRestore.current = null;
    cancelScrollAnimation();
    releaseHold();
    const bottom = geometry(el).bottom;
    const returned = top > prevTop + 1 && top >= bottom - 1;
    setFollow(returned);
    if (el.scrollTop > bottom) snapTo(el, bottom);
    if (returned) {
      readingLandmark.current = null;
      setPendingNew(false);
      setConsentPill(false);
    } else {
      rememberReader(el);
    }
    refreshAffordance(el);
  }

  function jumpToBottom() {
    const el = scrollerRef.current;
    if (!el) return;
    releaseHold();
    readingLandmark.current = null;
    setFollow(true);
    setPendingNew(false);
    setConsentPill(false);
    animateScroll(el, geometry(el).bottom);
    refreshAffordance(el);
  }

  function jumpToConsent() {
    const el = scrollerRef.current;
    if (!el) return;
    const card = el.querySelector<HTMLElement>('[data-testid="consent-card"], [data-testid="question-card"]');
    releaseHold();
    setConsentPill(false);
    readingLandmark.current = null;
    setFollow(true);
    if (card && typeof card.scrollIntoView === "function") {
      cancelScrollAnimation();
      autoScrolling.current = true;
      card.scrollIntoView({ block: "center" });
      lastScrollTop.current = el.scrollTop;
      const epoch = scrollEpoch.current;
      requestAnimationFrame(() => {
        if (epoch === scrollEpoch.current) autoScrolling.current = false;
        refreshAffordance(el);
      });
    } else {
      jumpToBottom();
    }
  }

  async function handleLoadEarlier() {
    if (!onLoadEarlier || loadingOlder || !hasMoreOlder) return;
    const el = scrollerRef.current;
    if (el && !followRef.current && !anchorActive.current) rememberReader(el);
    try {
      await onLoadEarlier();
    } catch {
      // Keep the existing viewport when the older-page request fails.
    }
  }

  const lastAsstIdx = useMemo(() => lastAssistantIndex(messages), [messages]);

  // FIX122 Part A — the render mode changes only between turns. Crossing the
  // threshold swaps one list component for another, which remounts every
  // row; a send is exactly what crosses it (48 rows + the new pair), and the
  // swap then landed in the middle of the send's anchor jump, under the row
  // that had just started streaming.
  const wantVirtual = messages.length >= VIRTUALIZE_THRESHOLD;
  const virtualMode = useRef(wantVirtual);
  const modeConv = useRef(convId);
  if (modeConv.current !== convId || !anyStreaming) {
    modeConv.current = convId;
    virtualMode.current = wantVirtual;
  }
  const shouldVirtualize = virtualMode.current;

  return (
    <div className="relative flex-1 overflow-hidden">
      {/* SR-only live region — the single announcement source for the
          thread (start/phase-change/finish). Never wraps the streaming
          markdown body: per-token announcements would flood the reader. */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
      <div
        ref={attachScroller}
        onScroll={onScroll}
        onWheel={onWheel}
        onTouchMove={markUserIntent}
        onKeyDown={(e) => {
          if (SCROLL_KEYS.has(e.key) && !(e.target as HTMLElement).closest("input, textarea, select, button, [contenteditable=true]")) markUserIntent();
        }}
        onPointerDown={(e) => {
          // the scrollbar is the only part of the scroller itself one can press
          if (e.target === e.currentTarget) markUserIntent();
        }}
        className="scrollbar-thin h-full overflow-y-auto px-4 py-6 md:px-10"
        // FIX130 C4 — the credits badge floats over the bottom of this
        // column; keep its reach (+8 px air) free under the last message so
        // it never sits on the last timestamp or the last line.
        style={{ overflowAnchor: "none", paddingBottom: Math.max(THREAD_BOTTOM_PAD, badgeReach + 8) }}
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
              {shouldVirtualize ? scrollerEl && (
                <VirtualMessageList
                  scrollerEl={scrollerEl}
                  // A thread opens at its newest message, and a list that
                  // turns virtual between turns does so under a reader who
                  // is either following or reading somewhere — keep both.
                  startAtEnd={followRef.current || anchorActive.current}
                  preservedKey={readingLandmark.current?.key ?? null}
                  onLayout={() => reconcileRef.current()}
                  onScrollWrite={() => {
                    const el = scrollerRef.current;
                    if (el) lastScrollTop.current = el.scrollTop;
                  }}
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
      ) : waitingQuestion ? (
        <div
          role="status"
          data-testid="waiting-question-notice"
          className="popover-appear absolute bottom-16 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-primary shadow-lg backdrop-blur-sm"
        >
          {t("thread.waitingQuestion")}
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

      {consentPill && (waitingConsent || waitingQuestion) ? (
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
          {waitingConsent ? t("thread.permissionNeeded") : t("thread.questionPill")}
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
  return `${acts.length}/${finished}|${m.plan?.length ?? 0}|${m.code_runs?.length ?? 0}|${m.pending_consent?.promptId ?? ""}|${m.pending_question?.promptId ?? ""}`;
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
    /** The mounted scroller. Never null: see ChatThread's attachScroller. */
    scrollerEl: HTMLDivElement;
    /** Where the list starts: at the newest row, or where the scroller is. */
    startAtEnd: boolean;
    preservedKey: string | null;
    /** Called in the layout phase of every render of the list — i.e. every
     *  time row positions may have moved — so the thread's one position
     *  keeper (reconcile) runs in the same commit, before paint. */
    onLayout: () => void;
    onScrollWrite: () => void;
  },
) {
  const { scrollerEl, messages } = props;
  const preservedIndex = messages.findIndex((m) => rowKey(m) === props.preservedKey);
  // Read once: these seed the FIRST render, before the virtualizer has
  // observed anything. Without a rect it renders no rows until its
  // ResizeObserver reports; without an offset it starts — and, on attaching,
  // scrolls the element — at 0.
  const initial = useRef({
    rect: { width: scrollerEl.clientWidth, height: scrollerEl.clientHeight },
    offset: virtualInitialOffset(props.startAtEnd, scrollerEl.scrollTop),
  }).current;
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollerEl,
    initialRect: initial.rect,
    initialOffset: initial.offset,
    estimateSize: () => 200,
    overscan: 5,
    getItemKey: (index) => rowKey(messages[index]),
    rangeExtractor(range) {
      const indexes = defaultRangeExtractor(range);
      // A prepend can move the reader's row outside the estimated window.
      // Keep that landmark measurable until the viewport has been restored.
      if (preservedIndex >= 0 && !indexes.includes(preservedIndex)) {
        indexes.push(preservedIndex);
        indexes.sort((a, b) => a - b);
      }
      return indexes;
    },
    scrollToFn(offset, options, instance) {
      elementScroll(offset, options, instance);
      props.onScrollWrite();
    },
    measureElement(element) {
      // Some browsers report 0 during a transition; the virtualizer
      // tolerates it but a measured pixel value is preferable.
      return element?.getBoundingClientRect().height ?? 0;
    },
  });
  // FIX122 Part A — ONE keeper of the reader's position. A row above the
  // viewport that turns out taller than its 200 px estimate moves
  // everything under it; the virtualizer answers that by rewriting
  // scrollTop from ITS cached offset, and the thread answers it by putting
  // the row the person is reading back where it was (reconcile's landmark).
  // After a page of older messages both fired, the virtualizer's from an
  // offset the thread had just replaced: measured on the real page, the row
  // being read was up to 155 px out of place for a frame (19 px with this
  // off). The landmark is the one that knows what the person is looking at
  // (and the follow / send-anchor states the virtualizer has no idea of), so
  // it is the one that stays.
  virtualizer.shouldAdjustScrollPositionOnItemSizeChange = neverAdjust;
  const onLayout = props.onLayout;
  useLayoutEffect(() => {
    onLayout();
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

/** The offset a freshly mounted virtual list starts from. "The end" is a
 *  number past any real content: the virtualizer maps it to its last rows and
 *  the browser clamps the scroll write to the real bottom — no estimate of
 *  the total height is involved, so a wrong row estimate cannot land the
 *  first frame somewhere in the middle. */
/** FIX127 D2 — per-conversation scroll memory for this page session. Only
 *  a position AWAY from the bottom is worth keeping: a chat read to the
 *  end opens at the end (where new answers arrive). */
const scrollMemory = new Map<string, { top: number; far: boolean }>();
export function rememberScroll(convId: string, top: number, far: boolean): void {
  if (!convId) return;
  scrollMemory.set(convId, { top, far });
}
export function rememberedScrollFor(convId: string): { top: number; far: boolean } | undefined {
  return scrollMemory.get(convId);
}
export function _resetScrollMemory(): void {
  scrollMemory.clear();
}

export function virtualInitialOffset(startAtEnd: boolean, currentScrollTop: number): number {
  return startAtEnd ? VIRTUAL_END_OFFSET : Math.max(0, currentScrollTop);
}
const VIRTUAL_END_OFFSET = 1e9;
const neverAdjust = () => false;

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
    // FIX127 D4 — id="msg-<id>" is the deep-link anchor (/chat/<conv>#msg-<id>).
    <div key={rowKey(m)} id={`msg-${m.id}`} className={extraGap} data-msg-index={i}>
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
          m.status !== "regenerated" &&
          // FIX100 Part B — Regenerate re-sends the identical request, so
          // it is only an answer when a retry could work. A failure the
          // gateway called non-retryable ("change the request before
          // sending it again") gets the composer instead; see the notice
          // in MessageBubble. knownNotRetryable is silent — the button
          // stays — for a code we do not recognise and for a row read back
          // from the server, which carries no code at all.
          !knownNotRetryable(m.error_code, m.error_retryable)
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
