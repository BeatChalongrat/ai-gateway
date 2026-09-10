"use client";

// FIX76 — the shared frame for a panel that sits BESIDE the chat.
//
// Two modes, decided by the viewport (Part A Q1):
//
//   docked  (≥ lg, 1024 px) — a flex sibling of the thread column. No
//           backdrop, no focus trap, no `dialog`: the conversation stays
//           live (scroll, type, send) while the pane is open, so the
//           pane is a `complementary` landmark named after its panel.
//           Esc closes it only when focus is inside the pane or on the
//           document body — Esc in the composer must not take away a
//           pane the user is reading.
//   overlay (< lg) — the slide-over the three panels always had, made
//           honest: a backdrop that closes on click, `role="dialog"`
//           + `aria-modal`, a focus trap, and Esc always closes.
//
// Width when docked: min(maxWidth, 45 %) of the row by default — and
// FIX82 reverses FIX76's Part A Q6: the divider now DRAGS. The owner
// used the docked pane and wants to move the split, so the handle is a
// real control (7 px hit area, col-resize cursor, hover/drag state), a
// WAI-ARIA separator (role="separator", aria-valuenow/min/max, arrow
// keys, Home/End), double-click resets to the FIX76 default, and the
// chosen width is remembered per panel in localStorage (a per-viewer
// convenience: try/catch everywhere, an empty or throwing read renders
// the default). Bounds: the pane never goes under PANE_MIN_PX and the
// thread keeps THREAD_MIN_PX — enforced in CSS (min/max-width) so a
// window resize re-clamps without JS. Below `lg` the pane is an overlay
// dialog: no split, no handle, exactly as before. The measured docked
// width still publishes to sidePaneStore through the ResizeObserver, so
// floating pills and the credits badge track a drag CONTINUOUSLY.
//
// This component moves the FRAME only. What each panel renders inside
// (preview renderers, the canvas sandbox, the version rail) is the
// panel's own and unchanged.

import { useEffect, useRef, useState, type ReactNode } from "react";

import { X } from "lucide-react";

import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useT } from "@/lib/i18n";
import { useSidePaneStore } from "@/stores/sidePaneStore";

/** The docking breakpoint — Tailwind's `lg`, the next stop after the
 *  `md` the sidebar already uses. */
export const DOCK_MEDIA_QUERY = "(min-width: 1024px)";

export function useDocked(): boolean {
  const [docked, setDocked] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? true
      : window.matchMedia(DOCK_MEDIA_QUERY).matches,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(DOCK_MEDIA_QUERY);
    const handler = (e: MediaQueryListEvent) => setDocked(e.matches);
    mq.addEventListener("change", handler);
    setDocked(mq.matches);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return docked;
}

// FIX82 — the two floors. PANE_MIN keeps the pane useful (below ~320 the
// preview/canvas content is unreadable); THREAD_MIN keeps the thread
// readable (FIX76 measured it unreadable under ~400 px; 480 leaves room
// to breathe). At the narrowest `lg` window the two cannot both hold —
// the pane's floor wins (CSS min-width beats max-width), which at
// 1024 px still leaves the thread ~440 px, above the measured floor.
export const PANE_MIN_PX = 320;
export const THREAD_MIN_PX = 480;

/** Arrow-key step for the keyboard separator. */
export const RESIZE_STEP_PX = 16;

/** Clamp a desired pane width against the row: never under PANE_MIN_PX,
 *  never leaving the thread less than THREAD_MIN_PX (pane floor wins
 *  when the row is too narrow for both). Pure — pinned by tests. */
export function clampPaneWidth(desired: number, rowWidth: number): number {
  const upper = Math.max(PANE_MIN_PX, rowWidth - THREAD_MIN_PX);
  return Math.min(Math.max(Math.round(desired), PANE_MIN_PX), upper);
}

const WIDTH_STORE_PREFIX = "sran-pane-width:";

export function readSavedPaneWidth(key: string | undefined): number | null {
  if (!key) return null;
  try {
    const v = Number(window.localStorage.getItem(WIDTH_STORE_PREFIX + key));
    return Number.isFinite(v) && v >= PANE_MIN_PX ? Math.round(v) : null;
  } catch {
    return null;
  }
}

function savePaneWidth(key: string | undefined, px: number | null): void {
  if (!key) return;
  try {
    if (px == null) window.localStorage.removeItem(WIDTH_STORE_PREFIX + key);
    else window.localStorage.setItem(WIDTH_STORE_PREFIX + key, String(Math.round(px)));
  } catch {
    /* per-viewer convenience only */
  }
}

interface Props {
  open: boolean;
  /** Accessible name of the pane (and the header title unless `title` is given). */
  label: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  /** Header actions rendered before the close button. */
  actions?: ReactNode;
  /** Docked width cap in px (canvas/preview 672, workspace 448). */
  maxWidth: number;
  /** FIX82 — localStorage key suffix for the remembered drag width
   *  ("canvas" | "workspace" | "preview"). Omitted = draggable but not
   *  remembered. */
  widthKey?: string;
  closeLabel: string;
  onClose: () => void;
  testId?: string;
  children: ReactNode;
}

export function SidePane({ open, label, title, subtitle, icon, actions, maxWidth, widthKey, closeLabel, onClose, testId, children }: Props) {
  const t = useT();
  const docked = useDocked();
  const asideRef = useRef<HTMLElement>(null);
  const setDockedWidth = useSidePaneStore((s) => s.setDockedWidth);

  // FIX82 — the user-chosen width (null = the FIX76 default formula).
  // Seeded from localStorage; every change goes back there (try/catch —
  // a blocked or empty store just means the default renders).
  const [userWidth, setUserWidthState] = useState<number | null>(() => readSavedPaneWidth(widthKey));
  const [dragging, setDragging] = useState(false);
  // The row's width, for aria-valuemax and drag clamping; kept fresh by
  // the same observer effect that publishes the docked width. State (not
  // a ref) so aria-valuemax re-renders when the window changes.
  const [rowWidth, setRowWidth] = useState(0);
  const dragStart = useRef<{ x: number; width: number } | null>(null);
  const setUserWidth = (px: number | null) => {
    setUserWidthState(px);
    savePaneWidth(widthKey, px);
  };

  // Overlay mode is a dialog: trap focus while open. Docked mode never traps.
  useFocusTrap(asideRef, open && !docked);

  // Esc — always in overlay mode; docked only when focus is in the pane
  // or nowhere in particular (body).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!docked) {
        onClose();
        return;
      }
      const active = document.activeElement;
      const inside = asideRef.current?.contains(active) ?? false;
      if (inside || !active || active === document.body) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, docked, onClose]);

  // Publish the docked width (0 when closed or overlaid).
  useEffect(() => {
    const el = asideRef.current;
    if (!open || !docked || !el) {
      setDockedWidth(0);
      return;
    }
    const publish = () => {
      setDockedWidth(Math.round(el.getBoundingClientRect().width));
      setRowWidth(Math.round(el.parentElement?.getBoundingClientRect().width ?? 0));
    };
    publish();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    if (el.parentElement) ro.observe(el.parentElement);
    return () => {
      ro.disconnect();
      setDockedWidth(0);
    };
  }, [open, docked, setDockedWidth]);

  // FIX82 — the drag itself. Pointer capture keeps move events coming to
  // the handle even when the cursor crosses the canvas iframe (the
  // classic dead-drag bug); width updates per move, so the observer
  // publishes continuously and the floating pills track the drag live.
  const onHandlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = asideRef.current;
    if (!el) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragStart.current = { x: e.clientX, width: el.getBoundingClientRect().width };
    setDragging(true);
  };
  const onHandlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStart.current;
    if (!start) return;
    const row = rowWidth || window.innerWidth;
    // dragging left grows the right-hand pane
    setUserWidth(clampPaneWidth(start.width + (start.x - e.clientX), row));
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStart.current) return;
    dragStart.current = null;
    setDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  };
  const onHandleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const el = asideRef.current;
    if (!el) return;
    const row = rowWidth || window.innerWidth;
    const cur = Math.round(el.getBoundingClientRect().width);
    if (e.key === "ArrowLeft") setUserWidth(clampPaneWidth(cur + RESIZE_STEP_PX, row)); // divider left = pane grows
    else if (e.key === "ArrowRight") setUserWidth(clampPaneWidth(cur - RESIZE_STEP_PX, row));
    else if (e.key === "Home") setUserWidth(PANE_MIN_PX); // ARIA: Home = valuemin
    else if (e.key === "End") setUserWidth(clampPaneWidth(row, row)); // End = valuemax
    else if (e.key === "Enter") setUserWidth(null); // reset, same as double-click
    else return;
    e.preventDefault();
  };

  const header = (
    <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-4 py-3">
      {icon}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-text-primary">{title ?? label}</div>
        {subtitle ? <div className="truncate text-xs text-text-muted">{subtitle}</div> : null}
      </div>
      {actions}
      <button
        type="button"
        onClick={onClose}
        className="focus-ring rounded p-1 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary"
        aria-label={closeLabel}
        data-testid={testId ? `${testId}-close` : undefined}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );

  if (docked) {
    if (!open) return null;
    // A dragged width is clamped in CSS too (min/max-width), so shrinking
    // the window re-clamps the split without any JS; the default keeps
    // the FIX76 formula byte-identical.
    const widthStyle: React.CSSProperties =
      userWidth != null
        ? { width: userWidth, minWidth: PANE_MIN_PX, maxWidth: `max(${PANE_MIN_PX}px, calc(100% - ${THREAD_MIN_PX}px))` }
        : { width: `min(${maxWidth}px, 45%)` };
    return (
      <aside
        ref={asideRef}
        role="complementary"
        aria-label={label}
        data-testid={testId}
        data-pane-mode="docked"
        className="relative flex h-full shrink-0 flex-col border-l border-border bg-bg-surface"
        style={widthStyle}
      >
        {/* FIX82 — the splitter: a 7 px hit area straddling the border,
            visible on hover/drag, ARIA separator with arrow keys;
            double-click (or Enter) resets to the FIX76 default. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t("pane.resize")}
          aria-valuemin={PANE_MIN_PX}
          aria-valuemax={rowWidth > 0 ? Math.max(PANE_MIN_PX, rowWidth - THREAD_MIN_PX) : undefined}
          aria-valuenow={userWidth ?? undefined}
          tabIndex={0}
          data-testid={testId ? `${testId}-resizer` : "pane-resizer"}
          data-dragging={dragging ? "true" : undefined}
          onPointerDown={onHandlePointerDown}
          onPointerMove={onHandlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={() => setUserWidth(null)}
          onKeyDown={onHandleKeyDown}
          className="focus-ring absolute inset-y-0 -left-1 z-10 w-[7px] cursor-col-resize touch-none select-none"
        >
          <div
            aria-hidden
            className={`mx-auto h-full w-[3px] rounded transition-colors ${
              dragging ? "bg-border-strong" : "bg-transparent hover:bg-border-strong"
            }`}
          />
        </div>
        {header}
        {children}
      </aside>
    );
  }

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden
        data-testid={testId ? `${testId}-backdrop` : undefined}
      />
      <aside
        ref={asideRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-hidden={open ? undefined : true}
        data-testid={testId}
        data-pane-mode="overlay"
        className={`fixed right-0 top-0 z-50 flex h-full w-full flex-col border-l border-border bg-bg-surface shadow-2xl transition-transform duration-200 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        style={{ maxWidth }}
      >
        {header}
        {children}
      </aside>
    </>
  );
}
