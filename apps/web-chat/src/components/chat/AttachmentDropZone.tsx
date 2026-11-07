"use client";

// Wraps the chat thread area in a full-bleed drag-and-drop zone.
// On dragenter: overlays the area with a dashed border + label.
// On drop: forwards each File to ``onFiles``. Counter-based hover
// tracking handles the dragenter/dragleave child-event problem.

import { useCallback, useRef, useState } from "react";

import { useT } from "@/lib/i18n";

interface Props {
  onFiles: (files: File[]) => void;
  enabled?: boolean;
  className?: string;
  children: React.ReactNode;
}

export function AttachmentDropZone({
  onFiles,
  enabled = true,
  className,
  children,
}: Props) {
  const t = useT();
  const [over, setOver] = useState(false);
  // Counts entered children so child-element transitions don't trip
  // the overlay off mid-hover.
  const counter = useRef(0);

  const onDragEnter = useCallback(
    (e: React.DragEvent) => {
      if (!enabled) return;
      // Only react if a file is being dragged (not e.g. text from
      // another part of the page).
      if (!hasFiles(e)) return;
      e.preventDefault();
      counter.current += 1;
      setOver(true);
    },
    [enabled],
  );

  const onDragOver = useCallback(
    (e: React.DragEvent) => {
      if (!enabled) return;
      if (!hasFiles(e)) return;
      // preventDefault here is what tells the browser the page is a
      // valid drop target. Without it the cursor shows a "no drop"
      // indicator.
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    [enabled],
  );

  const onDragLeave = useCallback(
    (e: React.DragEvent) => {
      if (!enabled) return;
      counter.current = Math.max(0, counter.current - 1);
      if (counter.current === 0) setOver(false);
      e.preventDefault();
    },
    [enabled],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      if (!enabled) return;
      e.preventDefault();
      counter.current = 0;
      setOver(false);
      const files = Array.from(e.dataTransfer.files ?? []);
      if (files.length > 0) onFiles(files);
    },
    [enabled, onFiles],
  );

  return (
    <div
      className={`relative ${className ?? ""}`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {children}
      {over ? (
        <div
          className="pointer-events-none absolute inset-2 z-20 flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-border-strong bg-bg-surface text-text-primary backdrop-blur-sm"
          aria-hidden
        >
          <svg width={36} height={36} viewBox="0 0 24 24" fill="none">
            <path
              d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <div className="mt-2 text-sm font-medium">{t("drop.title")}</div>
          <div className="mt-0.5 text-[11px] text-text-muted">
            PDF · DOCX · XLSX · CSV · TXT · PNG · JPG
          </div>
        </div>
      ) : null}
    </div>
  );
}

function hasFiles(e: React.DragEvent): boolean {
  const types = e.dataTransfer?.types;
  if (!types) return false;
  // DataTransfer.types is a DOMStringList in Safari; iterate.
  for (let i = 0; i < types.length; i++) {
    if (types[i] === "Files") return true;
  }
  return false;
}
