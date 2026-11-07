// FIX132 C — deleting a chat says what really happens: it disappears now and
// is permanently removed within the server's window (30 days by default),
// with backups rolling off after that. It used to say only "this cannot be
// undone" — while the rows stayed in the database for good.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      getDataPolicy: vi.fn(async () => ({
        deleted_data_purge: "on", deleted_data_purge_days: 30, backup_retention_days: 14,
        export_link_hours: 24, export_max_file_mb: 500, export_max_chats: 5000,
      })),
    },
  };
});

import { resetDataPolicyForTest } from "@/hooks/useDataPolicy";
import { dictionaries } from "@/lib/i18n/dict";
import { useI18nStore } from "@/lib/i18n";
import type { Conversation } from "@/lib/types";

import { ConversationItem } from "./ConversationItem";

const conv: Conversation = {
  id: "c1", title: "งบประมาณ", model_id: "gpt-4o-mini", message_count: 2,
  created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
};
const noop = () => undefined;

afterEach(cleanup);
beforeEach(() => {
  resetDataPolicyForTest();
  useI18nStore.setState({ locale: "th" });
});

describe("FIX132 — the delete-chat confirmation states the period", () => {
  it("says in Thai: gone now, permanently removed within 30 days, backups 14 days after", async () => {
    render(
      <ConversationItem conv={conv} active={false} onSelect={noop} onRename={noop} onDelete={noop} onTogglePin={noop} />,
    );
    fireEvent.click(screen.getByLabelText(dictionaries.th["a11y.conversationActionsFor"].replace("{title}", "งบประมาณ")));
    fireEvent.click(screen.getByText(dictionaries.th["conv.delete"]));
    await waitFor(() => expect(document.body.textContent).toContain("ภายใน 30 วัน"));
    const text = document.body.textContent ?? "";
    expect(text).toContain("หายไปทันที");
    expect(text).toContain("14 วัน");
    expect(text).not.toContain("{days}");
  });
});
