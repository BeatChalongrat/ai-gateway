"use client";

// CustomInstructionsModal — two free-form text fields that the
// chat-edge folds into the system prompt for every conversation.
//
// Shares its design language with VoiceSettingsModal: each question
// lives in its own ``bg-bg-base`` section card; the action row uses
// the same Cancel / inverted-primary Save buttons; spacing /
// typography / corner radii all match.
//
// Two persistence paths:
//   1. Debounced auto-save (1s idle) — protects against backdrop
//      dismissal losing the edit.
//   2. Explicit Save button — surfaces the success / failure toast
//      and closes the modal.

import { useEffect, useRef, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

import { SettingsCard } from "./VoiceSettingsModal";

interface Props {
  open: boolean;
  onClose: () => void;
}

const MAX_CHARS = 1500;

export function CustomInstructionsModal({ open, onClose }: Props) {
  const t = useT();
  const [about, setAbout] = useState("");
  const [respond, setRespond] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Debounced auto-save handle. We still expose an explicit Save
  // button for clarity, but typing also persists silently after a
  // 1s idle so the user doesn't lose changes if they close via
  // backdrop click.
  const autoSaveHandle = useRef<number | null>(null);
  const dirtyRef = useRef(false);

  // Lazy-load on open. We re-fetch every time so a save from
  // another tab / device flows through, but only once per open
  // cycle (loaded gate).
  useEffect(() => {
    if (!open || loaded) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const p = await api.getPreferences();
        if (cancelled) return;
        setAbout(p.custom_instructions_about ?? "");
        setRespond(p.custom_instructions_respond ?? "");
      } catch {
        if (!cancelled) toast("Couldn't load preferences.");
      } finally {
        if (!cancelled) {
          setLoading(false);
          setLoaded(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, loaded]);

  // Reset the loaded gate whenever the modal closes so the next
  // open re-fetches.
  useEffect(() => {
    if (!open) setLoaded(false);
  }, [open]);

  // Debounced background save. Fires 1s after the last edit while
  // the modal is open + dirty. The visible Save button is still
  // available for users who want an explicit confirmation.
  useEffect(() => {
    if (!open || !loaded || !dirtyRef.current) return;
    if (autoSaveHandle.current != null) {
      window.clearTimeout(autoSaveHandle.current);
    }
    autoSaveHandle.current = window.setTimeout(() => {
      autoSaveHandle.current = null;
      if (!dirtyRef.current) return;
      dirtyRef.current = false;
      void api
        .putPreferences({
          custom_instructions_about: about,
          custom_instructions_respond: respond,
        })
        .catch(() => {
          /* surface only on explicit save */
        });
    }, 1000);
    return () => {
      if (autoSaveHandle.current != null) {
        window.clearTimeout(autoSaveHandle.current);
        autoSaveHandle.current = null;
      }
    };
  }, [about, respond, open, loaded]);

  async function save() {
    if (about.length > MAX_CHARS || respond.length > MAX_CHARS) {
      toast(`Each field is limited to ${MAX_CHARS} characters.`);
      return;
    }
    setSaving(true);
    try {
      await api.putPreferences({
        custom_instructions_about: about,
        custom_instructions_respond: respond,
      });
      toast("Custom instructions saved.");
      onClose();
    } catch {
      toast("Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("instr.title")}
      description={t("instr.subtitle")}
      maxWidth={560}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded-lg border border-border bg-transparent px-4 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            {t("instr.cancel")}
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || loading}
            className="focus-ring rounded-lg bg-text-primary px-6 py-2 text-sm font-medium text-bg-base transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {saving ? t("instr.saving") : t("instr.save")}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <SettingsCard title={t("instr.about.label")}>
          <InstructionField
            placeholder={t("instr.about.placeholder")}
            value={about}
            onChange={(v) => {
              dirtyRef.current = true;
              setAbout(v);
            }}
            loading={loading}
          />
        </SettingsCard>
        <SettingsCard title={t("instr.respond.label")}>
          <InstructionField
            placeholder={t("instr.respond.placeholder")}
            value={respond}
            onChange={(v) => {
              dirtyRef.current = true;
              setRespond(v);
            }}
            loading={loading}
          />
        </SettingsCard>
      </div>
    </Modal>
  );
}

function InstructionField({
  placeholder,
  value,
  onChange,
  loading,
}: {
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  loading: boolean;
}) {
  const remaining = MAX_CHARS - value.length;
  return (
    <div className="flex flex-col gap-1.5">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={5}
        disabled={loading}
        maxLength={MAX_CHARS}
        // bg-bg-surface here, NOT bg-bg-base — the textarea sits
        // INSIDE a SettingsCard which is already bg-bg-base, so the
        // input reads as raised within the card. Border picks up on
        // focus; no accent ring.
        className="scrollbar-thin min-h-[120px] resize-y rounded-lg border border-border bg-bg-surface px-3 py-2.5 text-sm text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none focus:ring-0 disabled:opacity-50"
      />
      <span
        className={`text-right text-xs ${
          remaining < 0 ? "text-status-danger" : "text-text-muted"
        }`}
      >
        {value.length}/{MAX_CHARS}
      </span>
    </div>
  );
}
