// FIX84 Part D — "Disable user" asks first, naming the user; "Enable" stays
// one click. Before: PATCH /users/{id} fired on the first click, no confirm
// (FIX83 admin #1, S1).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { fetchAdminUsers, patchAdminUser } = vi.hoisted(() => ({
  fetchAdminUsers: vi.fn(),
  patchAdminUser: vi.fn(),
}));

vi.mock("../../lib/chatEdgeApi", () => ({ fetchAdminUsers, patchAdminUser }));

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";

import ChatUsersPage from "./page";

const user = (over: Partial<{ id: string; email: string; name: string; is_active: boolean }>) => ({
  id: "u1",
  email: "somchai@example.invalid",
  name: "Somchai Test",
  role: "member",
  is_active: true,
  conversation_count: 3,
  message_count: 12,
  token_count: 4000,
  last_active_at: "2026-09-08T00:00:00Z",
  created_at: "2026-08-01T00:00:00Z",
  ...over,
});

function renderPage() {
  return render(
    <I18nProvider>
      <ToastProvider>
        <ChatUsersPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  patchAdminUser.mockResolvedValue({});
});
afterEach(cleanup);

describe("chat-users deactivate confirm (FIX84 Part D)", () => {
  it("clicking Disable does NOT patch the user until the confirm names them and is accepted", async () => {
    fetchAdminUsers.mockResolvedValue({ users: [user({})], total: 1 });
    renderPage();
    const disable = await screen.findByTitle(/Disable user|ปิดใช้งานผู้ใช้/);
    fireEvent.click(disable);
    expect(patchAdminUser).not.toHaveBeenCalled();
    // the dialog names the user and says what happens
    const dialog = (await screen.findByText(/Disable this user?|ปิดใช้งานผู้ใช้รายนี้?/)).closest("div")!.parentElement!.parentElement as HTMLElement;
    expect(dialog.textContent).toContain("Somchai Test");
    expect(dialog.textContent).toMatch(/lose access|เข้าใช้แชทไม่ได้/);
    const confirm = Array.from(dialog.querySelectorAll("button")).find((b) => /Disable user|ปิดใช้งานผู้ใช้/.test(b.textContent || ""))!;
    fireEvent.click(confirm);
    await waitFor(() => expect(patchAdminUser).toHaveBeenCalledWith("u1", { is_active: false }));
  });

  it("Cancel in the dialog leaves the user untouched", async () => {
    fetchAdminUsers.mockResolvedValue({ users: [user({})], total: 1 });
    renderPage();
    fireEvent.click(await screen.findByTitle(/Disable user|ปิดใช้งานผู้ใช้/));
    const dialog = (await screen.findByText(/Disable this user?|ปิดใช้งานผู้ใช้รายนี้?/)).closest("div")!.parentElement!.parentElement as HTMLElement;
    const cancel = Array.from(dialog.querySelectorAll("button")).find((b) => /Cancel|ยกเลิก/.test(b.textContent || ""))!;
    fireEvent.click(cancel);
    await waitFor(() => expect(screen.queryByText(/Disable this user?|ปิดใช้งานผู้ใช้รายนี้?/)).toBeNull());
    expect(patchAdminUser).not.toHaveBeenCalled();
  });

  it("Enable (restoring access) stays a single click", async () => {
    fetchAdminUsers.mockResolvedValue({ users: [user({ is_active: false })], total: 1 });
    renderPage();
    fireEvent.click(await screen.findByTitle(/Enable user|เปิดใช้งานผู้ใช้/));
    await waitFor(() => expect(patchAdminUser).toHaveBeenCalledWith("u1", { is_active: true }));
    expect(screen.queryByText(/Disable this user?|ปิดใช้งานผู้ใช้รายนี้?/)).toBeNull();
  });
});
