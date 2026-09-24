// FIX132 C — "your data": what the server says happens to deleted data, and
// the shapes of the full export. The Settings page and the delete-chat
// confirmations state the period from here, so the words a user reads are
// the numbers the nightly sweep uses (GET /account/data-policy).

import type { DictKey } from "./i18n/dict";

export interface DataPolicy {
  /** on | dry_run | off — only "on" permanently removes anything. */
  deleted_data_purge: "on" | "dry_run" | "off";
  deleted_data_purge_days: number;
  backup_retention_days: number;
  export_link_hours: number;
  export_max_file_mb: number;
  export_max_chats: number;
}

/** What the server answers when nothing is configured (the owner's
 *  decision of 2026-09-24): purge on, 30 days, backups 14 days. Shown
 *  until the real answer arrives. */
export const DEFAULT_DATA_POLICY: DataPolicy = {
  deleted_data_purge: "on",
  deleted_data_purge_days: 30,
  backup_retention_days: 14,
  export_link_hours: 24,
  export_max_file_mb: 500,
  export_max_chats: 5000,
};

export type FullExportStatus = "pending" | "processing" | "completed" | "failed" | "expired";

export interface FullExport {
  id: string;
  status: FullExportStatus;
  size_bytes: number;
  requested_at: string;
  completed_at?: string;
  expires_at?: string;
}

/** Which dictionary sentences each surface uses (on = the purge runs,
 *  kept = the organisation turned it off). */
export const DELETION_NOTICE_KEYS = {
  sidebarConfirm: { on: "conv.deleteConfirm", kept: "conv.deleteConfirm.kept" },
  shortcutDialog: { on: "shortcuts.deleteConfirm.body", kept: "shortcuts.deleteConfirm.body.kept" },
  settings: { on: "settings.data.deleteChat.on", kept: "settings.data.deleteChat.kept" },
} as const satisfies Record<string, { on: DictKey; kept: DictKey }>;

/** The sentence a delete-chat confirmation shows: gone now, and when it is
 *  permanently removed — or, when the organisation turned the purge off,
 *  that it is not removed automatically. Never promises a removal the
 *  server will not do. */
export function chatDeletionNotice(
  policy: DataPolicy,
  keys: { on: DictKey; kept: DictKey },
): { key: DictKey; vars: Record<string, string> } {
  if (policy.deleted_data_purge === "on") {
    return {
      key: keys.on,
      vars: {
        days: String(policy.deleted_data_purge_days),
        backup: String(policy.backup_retention_days),
      },
    };
  }
  return { key: keys.kept, vars: {} };
}
