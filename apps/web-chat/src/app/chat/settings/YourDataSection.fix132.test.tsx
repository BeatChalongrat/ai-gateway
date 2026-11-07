// FIX132 C — the user owns their data: Settings → "ข้อมูลของคุณ".
//   - the delete-a-chat and delete-the-account periods are the server's
//     (GET /account/data-policy), in Thai, and a purge that is off is never
//     promised;
//   - "export all my data" starts a background export, shows it being
//     built, then the ZIP link and until when;
//   - "delete my account" is armed only by typing the account's email (no
//     window.prompt), shows the gateway's Thai refusal as it comes, and on
//     success says so and sends the person to sign in.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const m = vi.hoisted(() => ({
  policy: {
    deleted_data_purge: "on",
    deleted_data_purge_days: 30,
    backup_retention_days: 14,
    export_link_hours: 24,
    export_max_file_mb: 500,
    export_max_chats: 5000,
  } as Record<string, unknown>,
  exports: [] as unknown[],
  start: vi.fn(),
  del: vi.fn(),
  redirect: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      getDataPolicy: vi.fn(async () => m.policy),
      listFullExports: vi.fn(async () => ({ exports: m.exports })),
      startFullExport: m.start,
      exportDownloadUrl: (id: string) => `http://edge/api/v1/account/exports/${id}/download`,
    },
  };
});
vi.mock("@/lib/accountDeletion", () => ({ deleteAccount: m.del }));
vi.mock("@/lib/auth", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  redirectToPortalLogin: m.redirect,
}));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "somchai@acme.co", user_id: "u1" } }) }));

import { resetDataPolicyForTest } from "@/hooks/useDataPolicy";
import { useI18nStore } from "@/lib/i18n";

import { YourDataSection } from "./YourDataSection";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  resetDataPolicyForTest();
  useI18nStore.setState({ locale: "th" });
  m.policy.deleted_data_purge = "on";
  m.policy.deleted_data_purge_days = 30;
  m.exports = [];
});

describe("FIX132 — ข้อมูลของคุณ", () => {
  it("states the server's periods in Thai: gone now, removed within 30 days, backups 14 days later", async () => {
    render(<YourDataSection />);
    expect(screen.getByText("ข้อมูลของคุณ")).toBeTruthy();
    const chat = screen.getByTestId("your-data-delete-chat").textContent ?? "";
    expect(chat).toContain("หายไปทันที");
    expect(chat).toContain("ภายใน 30 วัน");
    expect(chat).toContain("14 วัน");
    expect(document.body.textContent).toContain("ไฟล์ ZIP");
    expect(document.body.textContent).not.toMatch(/\{(days|backup|mb|hours)\}/);
  });

  it("never promises a removal the server will not do", async () => {
    m.policy.deleted_data_purge = "off";
    render(<YourDataSection />);
    await waitFor(() =>
      expect(screen.getByTestId("your-data-delete-chat").textContent).toContain("ปิดการลบถาวรอัตโนมัติ"),
    );
    expect(screen.getByTestId("your-data-delete-chat").textContent).not.toContain("ภายใน 30 วัน");
  });

  it("builds the export in the background, then offers the ZIP and says until when", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    m.start.mockResolvedValue({ export: { id: "e1", status: "pending", size_bytes: 0, requested_at: "2026-09-24T01:00:00Z" } });
    render(<YourDataSection />);
    fireEvent.click(screen.getByTestId("export-start"));
    await waitFor(() => expect(screen.getByTestId("export-pending")).toBeTruthy());
    expect(m.start).toHaveBeenCalledTimes(1);

    m.exports = [{
      id: "e1", status: "completed", size_bytes: 2048, requested_at: "2026-09-24T01:00:00Z",
      completed_at: "2026-09-24T01:01:00Z", expires_at: "2026-09-25T01:01:00Z",
    }];
    await act(async () => {
      vi.advanceTimersByTime(3100);
    });
    await waitFor(() => expect(screen.getByTestId("export-ready")).toBeTruthy());
    const link = screen.getByTestId("export-download") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("http://edge/api/v1/account/exports/e1/download");
    expect(screen.getByTestId("export-ready").textContent).toContain("ดาวน์โหลดได้ถึง");
  });

  it("delete account: armed only by the typed email, never window.prompt, shows the Thai refusal", async () => {
    const prompt = vi.spyOn(window, "prompt");
    m.del.mockResolvedValue({
      ok: false, status: 409, code: "sole_owner_with_members",
      message: "ลบบัญชีนี้ไม่ได้ เพราะคุณเป็นเจ้าของทีมเพียงคนเดียว และทีมยังมีสมาชิกอีก 2 คน",
    });
    render(<YourDataSection />);
    fireEvent.click(screen.getByTestId("delete-account-open"));
    const confirm = screen.getByTestId("delete-account-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(document.body.textContent).toContain("ย้อนกลับไม่ได้");
    expect(document.body.textContent).toContain("30 วัน");

    const input = screen.getByTestId("delete-account-email");
    fireEvent.change(input, { target: { value: "somchai@acme.com" } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "  Somchai@Acme.co " } });
    expect(confirm.disabled).toBe(false);

    fireEvent.click(confirm);
    await waitFor(() => expect(screen.getByTestId("delete-account-error")).toBeTruthy());
    expect(m.del).toHaveBeenCalledWith("Somchai@Acme.co");
    expect(screen.getByTestId("delete-account-error").textContent).toContain("เจ้าของทีมเพียงคนเดียว");
    expect(m.redirect).not.toHaveBeenCalled();
    expect(prompt).not.toHaveBeenCalled();
  });

  it("delete account: on success says so, then sends the person to sign in", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    m.del.mockResolvedValue({ ok: true, purgeAfter: "2026-10-24T00:00:00Z", purgeDays: 30 });
    render(<YourDataSection />);
    fireEvent.click(screen.getByTestId("delete-account-open"));
    fireEvent.change(screen.getByTestId("delete-account-email"), { target: { value: "somchai@acme.co" } });
    fireEvent.click(screen.getByTestId("delete-account-confirm"));
    await waitFor(() => expect(screen.getByTestId("delete-account-done")).toBeTruthy());
    expect(m.redirect).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(m.redirect).toHaveBeenCalledTimes(1);
  });
});
