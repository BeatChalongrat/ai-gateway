"use client";

// FIX132 F29 — a new project is named before it exists. NIGHT3 p-01: the
// sidebar's "สร้างโปรเจกต์ใหม่" created "โปรเจกต์ใหม่" at once, silently — no
// dialog, the URL stayed /chat, focus stayed on the button. Now this asks
// for the name (as ChatGPT's "New project" does); the caller creates the
// project and opens it.

import { useEffect, useId, useRef, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { useT } from "@/lib/i18n";

const MAX_NAME = 100;

export function NewProjectDialog({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  /** Resolves when the project exists (the dialog then closes); rejects to keep it open. */
  onCreate: (name: string) => Promise<void>;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  useEffect(() => {
    if (!open) return;
    setName("");
    setBusy(false);
    // After the Modal's focus trap has placed focus on its first control.
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    inputRef.current?.focus();
    return () => window.clearTimeout(id);
  }, [open]);

  const trimmed = name.trim();
  async function create() {
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await onCreate(trimmed);
    } catch {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("project.new")}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded-md px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={() => void create()}
            disabled={!trimmed || busy}
            className="focus-ring rounded-md bg-text-primary px-3 py-1.5 text-xs font-medium text-bg-base hover:opacity-90 disabled:opacity-40"
          >
            {t("project.createAction")}
          </button>
        </>
      }
    >
      <label htmlFor={inputId} className="block text-xs font-medium text-text-secondary">
        {t("project.nameLabel")}
      </label>
      <input
        id={inputId}
        ref={inputRef}
        type="text"
        value={name}
        maxLength={MAX_NAME}
        placeholder={t("project.namePlaceholder")}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void create();
          }
        }}
        className="focus-ring mt-1.5 w-full rounded-md border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
      />
    </Modal>
  );
}
