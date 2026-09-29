// AM6 part B — reminders and notifications, on the web.
//
// The owner: nothing runs by itself. A reminder asks a person to feed the
// agent: when it falls due the server opens a round that waits for files and
// tells the person — in the app, on the desktop, in the browser once they
// allowed it. Snooze · skip this month · stop. One follow-up, then silence.
//
//   * The settings page lists an install's reminders, adds one, and snoozes,
//     skips, stops, starts again or deletes each; it says what reaches the
//     person and that nothing runs without them. The browser's permission is
//     asked for only when a person adds their first reminder.
//   * One host in the chat's layout shows the newest notification as the
//     chat's own banner strip, until it is acted on or closed.
//   * ?run=<id> brings that round into view.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, nav, hosted } = vi.hoisted(() => ({
  raw: vi.fn(),
  nav: { push: vi.fn(), replace: vi.fn() },
  hosted: { now: false },
}));
vi.mock("@/lib/api", () => ({
  api: { raw, uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), getKnowledgeAvailability: vi.fn().mockResolvedValue({ available: false }), listKnowledgeBases: vi.fn().mockResolvedValue({ knowledge_bases: [] }) },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  usePathname: () => "/chat",
}));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hosted.now }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ isSupported: false, start: vi.fn(), stop: vi.fn() }) }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => ({ isSupported: false, isTranscribing: false, start: vi.fn(), stop: vi.fn() }) }));

import AgentWorkspaceRoute from "@/app/chat/agents/i/[installId]/page";
import { useI18nStore } from "@/lib/i18n";

import { AgentNotifications } from "./notify/AgentNotifications";
import { SettingsPage } from "./settings/SettingsPage";
import type { AmNotification, Reminder, ThreadItem } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";

const NOTE = "ไม่มีอะไรทำงานเองโดยไม่มีคน: การเตือนเปิดรอบที่รอไฟล์ไว้ให้ และรอบจะเริ่มเมื่อคุณส่งไฟล์";

const reminder = (o: Partial<Reminder> = {}): Reminder => ({
  id: "rem-1",
  install_id: "i-1",
  label: "ปิดงบประจำเดือน",
  rule: { every: "month", day: 5, time: "09:00" },
  rule_text: "ทุกเดือน วันที่ 5 เวลา 09:00",
  status: "active",
  next_due_at: "2026-10-05T02:00:00Z",
  next_due_text: "5 ต.ค. 2569 09:00",
  snoozed_until: null,
  skipped_period: null,
  waiting_run_id: "r-9",
  period_label: "กันยายน 2569",
  ...o,
});

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => (body === null ? "" : JSON.stringify(body)) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
let enabled = true;
const callsTo = (path: string, method = "GET") =>
  raw.mock.calls.filter((c) => c[0] === path && ((c[1] as RequestInit | undefined)?.method ?? "GET") === method);
const bodyOf = (c: unknown[]) => JSON.parse(String((c[1] as RequestInit).body));

class FakeNotification {
  static permission: NotificationPermission = "default";
  static requestPermission = vi.fn(async () => "granted" as NotificationPermission);
  static made: FakeNotification[] = [];
  onclick: ((this: FakeNotification, ev: Event) => unknown) | null = null;
  close = vi.fn();
  constructor(
    public title: string,
    public options?: NotificationOptions,
  ) {
    FakeNotification.made.push(this);
  }
}

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  nav.push.mockReset();
  nav.replace.mockReset();
  hosted.now = false;
  enabled = true;
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) =>
    path === "/agent-market/availability" ? json({ available: true, runner: { healthy: true }, role: "member" }) : handler(path, init),
  );
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled }) })));
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  FakeNotification.permission = "default";
  FakeNotification.requestPermission.mockClear();
  FakeNotification.made = [];
  vi.stubGlobal("Notification", FakeNotification);
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ─────────────────────────── the settings page ───────────────────────────

describe("AM6 — เตือนให้ทำรอบ on the settings page", () => {
  let list: Reminder[];
  let reaches: Record<string, boolean>;
  const page: () => Handler = () => (path, init) => {
    const method = init?.method ?? "GET";
    if (path === "/agent-market/installs/i-1")
      return json({ install: { id: "i-1", slug: "sran.bank-reconciliation", title: "กระทบยอดธนาคาร", definition_version: "1.0.0", created_at: "" }, manifest: {}, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path === "/agent-market/installs/i-1/reminders" && method === "GET") return json({ reminders: list, reaches, note: NOTE });
    if (path === "/agent-market/installs/i-1/reminders" && method === "POST") {
      const b = JSON.parse(String(init!.body));
      return json({ reminder: reminder({ id: "rem-new", label: b.label, rule: b.rule, rule_text: "ทุกสัปดาห์ วันจันทร์ เวลา 08:30", waiting_run_id: null, period_label: null }) }, 201);
    }
    const act = /^\/agent-market\/reminders\/(rem-\w+)\/(snooze|skip|stop|resume)$/.exec(path);
    if (act && method === "POST") {
      const status = act[2] === "stop" ? "stopped" : "active";
      return json({ reminder: reminder({ id: act[1]!, status, next_due_text: act[2] === "snooze" ? "8 ต.ค. 2569 09:00" : "5 พ.ย. 2569 09:00" }) });
    }
    if (/^\/agent-market\/reminders\/rem-\w+$/.test(path) && method === "DELETE") return json(null, 204);
    if (path === "/agent-market/installs/i-1/defaults") return json({ defaults: [] });
    if (path === "/agent-market/theme") return json({ theme: null, version: 0, presets: [] });
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  };
  beforeEach(() => {
    list = [reminder()];
    reaches = { in_app: true, desktop: true, browser: false, email: false };
    handler = page();
  });

  it("lists each reminder: its rule, when it is next, its state, the round that waits and a way to it", async () => {
    render(<SettingsPage installId="i-1" />);
    const section = await screen.findByTestId("am-reminders");
    expect(within(section).getByRole("heading", { level: 2 }).textContent).toBe("เตือนให้ทำรอบ");
    const row = await within(section).findByTestId("am-reminder");
    expect(row.textContent).toContain("ปิดงบประจำเดือน");
    expect(row.textContent).toContain("ทุกเดือน วันที่ 5 เวลา 09:00");
    expect(row.textContent).toContain("5 ต.ค. 2569 09:00");
    expect(row.textContent).toContain("เปิดอยู่");
    const waiting = within(row).getByTestId("am-reminder-waiting");
    expect(waiting.textContent).toContain("รอบ กันยายน 2569 รอไฟล์อยู่");
    expect(within(waiting).getByRole("link").getAttribute("href")).toBe("/chat/agents/i/i-1?run=r-9");
  });

  it("says that nothing runs without a person, and what reaches them", async () => {
    render(<SettingsPage installId="i-1" />);
    const section = await screen.findByTestId("am-reminders");
    await waitFor(() => expect(within(section).getByTestId("am-reminders-note").textContent).toBe(NOTE));
    expect(within(section).getByTestId("am-reminders-reaches").textContent).toBe("แจ้งในแอป · แจ้งบนเดสก์ท็อป (เมื่อแอปเปิดอยู่ แม้ปิดหน้าต่าง) · ยังไม่ส่งอีเมล");
  });

  it("snooze 1, 3 or 7 days; skip this round; stop and start again; delete", async () => {
    render(<SettingsPage installId="i-1" />);
    const row = await screen.findByTestId("am-reminder");
    fireEvent.click(within(row).getByRole("button", { name: "เลื่อน" }));
    expect(within(row).getAllByRole("button", { name: /^เลื่อน \d วัน$/ }).map((b) => b.textContent)).toEqual(["เลื่อน 1 วัน", "เลื่อน 3 วัน", "เลื่อน 7 วัน"]);
    fireEvent.click(within(row).getByRole("button", { name: "เลื่อน 3 วัน" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/snooze", "POST")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/reminders/rem-1/snooze", "POST")[0]!)).toEqual({ days: 3 });
    await waitFor(() => expect(row.textContent).toContain("8 ต.ค. 2569 09:00"));

    fireEvent.click(within(row).getByRole("button", { name: "ข้ามรอบนี้" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/skip", "POST")).toHaveLength(1));

    fireEvent.click(within(row).getByRole("button", { name: "หยุด" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/stop", "POST")).toHaveLength(1));
    await waitFor(() => expect(row.textContent).toContain("หยุดอยู่"));
    // a stopped reminder is not snoozed or skipped: it can start again
    expect(within(row).queryByRole("button", { name: "เลื่อน" })).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "เปิดอีกครั้ง" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/resume", "POST")).toHaveLength(1));

    fireEvent.click(within(row).getByRole("button", { name: "ลบ" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1", "DELETE")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByTestId("am-reminder")).toBeNull());
  });

  it("adds one, every month on a day or every week on a weekday, at a time", async () => {
    render(<SettingsPage installId="i-1" />);
    const form = await screen.findByTestId("am-reminder-form");
    fireEvent.change(within(form).getByLabelText("ชื่อการเตือน"), { target: { value: "ส่งรายการเดินบัญชี" } });
    fireEvent.change(within(form).getByLabelText("เตือน"), { target: { value: "week" } });
    fireEvent.change(within(form).getByLabelText("วัน"), { target: { value: "1" } });
    fireEvent.change(within(form).getByLabelText("เวลา (เวลาไทย)"), { target: { value: "08:30" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/reminders", "POST")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/installs/i-1/reminders", "POST")[0]!)).toEqual({ label: "ส่งรายการเดินบัญชี", rule: { every: "week", weekday: 1, time: "08:30" } });
    await waitFor(() => expect(screen.getAllByTestId("am-reminder")).toHaveLength(2));

    fireEvent.change(within(form).getByLabelText("ชื่อการเตือน"), { target: { value: "ปิดงบ" } });
    fireEvent.change(within(form).getByLabelText("เตือน"), { target: { value: "month" } });
    fireEvent.change(within(form).getByLabelText("วันที่"), { target: { value: "31" } });
    fireEvent.change(within(form).getByLabelText("เวลา (เวลาไทย)"), { target: { value: "17:00" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/reminders", "POST")).toHaveLength(2));
    expect(bodyOf(callsTo("/agent-market/installs/i-1/reminders", "POST")[1]!)).toEqual({ label: "ปิดงบ", rule: { every: "month", day: 31, time: "17:00" } });
  });

  it("a reminder without a name or a time is not sent", async () => {
    render(<SettingsPage installId="i-1" />);
    const form = await screen.findByTestId("am-reminder-form");
    fireEvent.change(within(form).getByLabelText("เวลา (เวลาไทย)"), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    expect(within(form).getByRole("alert").textContent).toBe("ตั้งชื่อการเตือนก่อน");
    fireEvent.change(within(form).getByLabelText("ชื่อการเตือน"), { target: { value: "ปิดงบ" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    expect(within(form).getByRole("alert").textContent).toBe("ใส่เวลาเป็น ชั่วโมง:นาที เช่น 09:00");
    expect(callsTo("/agent-market/installs/i-1/reminders", "POST")).toHaveLength(0);
  });

  it("asks the browser's permission when the first reminder is added — never on page load", async () => {
    list = [];
    render(<SettingsPage installId="i-1" />);
    const form = await screen.findByTestId("am-reminder-form");
    await screen.findByTestId("am-reminders-note");
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    fireEvent.change(within(form).getByLabelText("ชื่อการเตือน"), { target: { value: "ปิดงบ" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/reminders", "POST")).toHaveLength(1));
  });

  it("does not ask again for a second reminder, nor in the desktop app", async () => {
    render(<SettingsPage installId="i-1" />);
    let form = await screen.findByTestId("am-reminder-form");
    await screen.findByTestId("am-reminder");
    fireEvent.change(within(form).getByLabelText("ชื่อการเตือน"), { target: { value: "ปิดงบ" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/reminders", "POST")).toHaveLength(1));
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    cleanup();

    hosted.now = true;
    list = [];
    render(<SettingsPage installId="i-1" />);
    form = await screen.findByTestId("am-reminder-form");
    await screen.findByTestId("am-reminders-note");
    fireEvent.change(within(form).getByLabelText("ชื่อการเตือน"), { target: { value: "ปิดงบ" } });
    fireEvent.click(within(form).getByRole("button", { name: "เพิ่มการเตือน" }));
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/reminders", "POST")).toHaveLength(2));
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
  });

  it("a refusal is said where it happened", async () => {
    const base = page();
    handler = (path, init) =>
      path === "/agent-market/reminders/rem-1/skip"
        ? json({ error: { code: "NOTHING_TO_SKIP", message: "ไม่มีรอบให้ข้ามในเดือนนี้" } }, 409)
        : base(path, init);
    render(<SettingsPage installId="i-1" />);
    const row = await screen.findByTestId("am-reminder");
    fireEvent.click(within(row).getByRole("button", { name: "ข้ามรอบนี้" }));
    expect((await within(row).findByRole("alert")).textContent).toBe("ไม่มีรอบให้ข้ามในเดือนนี้");
  });
});

// ─────────────────────────── the notification host ───────────────────────────

describe("AM6 — notifications in the app", () => {
  const LINK_DUE = "/chat/agents/i/i-1?thread=t-1&run=r-9";
  const LINK_WAIT = "/chat/agents/i/i-2?thread=t-4&run=r-3";
  const due: AmNotification = {
    id: "n-2", kind: "reminder_due", title: "ถึงเวลาปิดงบ ก.ย. 2569 แล้ว", body: "ต้องใช้ 3 ไฟล์นี้: บัญชีแยกประเภท · งบทดลอง · งบประมาณ",
    link: LINK_DUE, install_id: "i-1", run_id: "r-9", reminder_id: "rem-1", agent_name: "ปิดงบ", created_at: "2026-10-05T02:00:00Z",
  };
  const waiting: AmNotification = {
    id: "n-1", kind: "waiting", title: "กระทบยอดธนาคารรอคำตอบของคุณ", body: "รอบกันยายน 2569 ถามไว้ 2 ข้อ",
    link: LINK_WAIT, install_id: "i-2", run_id: "r-3", reminder_id: null, agent_name: "กระทบยอดธนาคาร", created_at: "2026-10-04T08:00:00Z",
  };
  let pending: AmNotification[];
  const host: () => Handler = () => (path, init) => {
    const method = init?.method ?? "GET";
    if (path === "/agent-market/notifications?surface=web") return json({ notifications: pending });
    if (/^\/agent-market\/notifications\/n-\d\/(shown|dismiss)$/.test(path) && method === "POST") {
      // shown on this surface, or dismissed: the server lists it no more
      const id = path.split("/")[3];
      pending = pending.filter((n) => n.id !== id);
      return json(null, 204);
    }
    if (/^\/agent-market\/reminders\/rem-1\/(snooze|skip|stop)$/.test(path) && method === "POST") return json({ reminder: reminder() });
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  };
  beforeEach(() => {
    pending = [due, waiting];
    handler = host();
  });

  it("is mounted once, in the chat's layout", () => {
    const layout = readFileSync(resolve(__dirname, "../../app/chat/layout.tsx"), "utf8");
    expect(layout.match(/<AgentNotifications \/>/g)).toHaveLength(1);
  });

  it("asks for nothing when Agent Market is not available", async () => {
    enabled = false;
    render(<AgentNotifications />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(raw.mock.calls.some((c) => String(c[0]).startsWith("/agent-market/notifications"))).toBe(false);
    expect(screen.queryByTestId("am-notification")).toBeNull();
  });

  it("shows the newest as the chat's banner strip, and tells the server it was shown here", async () => {
    render(<AgentNotifications />);
    const banner = await screen.findByTestId("am-notification");
    expect(banner.textContent).toContain(due.title);
    expect(banner.textContent).toContain(due.body);
    expect(banner.className).toContain("rounded-lg");
    expect(banner.getAttribute("role")).toBe("status");
    await waitFor(() => expect(callsTo("/agent-market/notifications/n-2/shown", "POST")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/notifications/n-2/shown", "POST")[0]!)).toEqual({ surface: "web" });
    expect(callsTo("/agent-market/notifications/n-1/shown", "POST")).toHaveLength(0);
  });

  it("stays until it is acted on or closed, whatever the next read says", async () => {
    pending = [due];
    render(<AgentNotifications />);
    await screen.findByTestId("am-notification");
    await waitFor(() => expect(callsTo("/agent-market/notifications/n-2/shown", "POST")).toHaveLength(1));
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(callsTo("/agent-market/notifications?surface=web").length).toBeGreaterThan(1);
    expect(screen.getByTestId("am-notification").textContent).toContain(due.title);
  });

  it("a reminder's: open, snooze a day, skip this round, stop, close", async () => {
    render(<AgentNotifications />);
    const banner = await screen.findByTestId("am-notification");
    const names = within(banner).getAllByRole("button").map((b) => b.getAttribute("aria-label") || b.textContent);
    expect(names).toEqual(["เปิด", "เลื่อน 1 วัน", "ข้ามรอบนี้", "หยุดเตือน", "ปิด"]);
    fireEvent.click(within(banner).getByRole("button", { name: "เลื่อน 1 วัน" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/snooze", "POST")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/reminders/rem-1/snooze", "POST")[0]!)).toEqual({ days: 1 });
    await waitFor(() => expect(callsTo("/agent-market/notifications/n-2/dismiss", "POST")).toHaveLength(1));
    // the next one takes its place
    await waitFor(() => expect(screen.getByTestId("am-notification").textContent).toContain(waiting.title));
  });

  it("skip and stop go to the reminder", async () => {
    render(<AgentNotifications />);
    fireEvent.click(within(await screen.findByTestId("am-notification")).getByRole("button", { name: "ข้ามรอบนี้" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/skip", "POST")).toHaveLength(1));
    cleanup();
    pending = [due];
    render(<AgentNotifications />);
    fireEvent.click(within(await screen.findByTestId("am-notification")).getByRole("button", { name: "หยุดเตือน" }));
    await waitFor(() => expect(callsTo("/agent-market/reminders/rem-1/stop", "POST")).toHaveLength(1));
  });

  it("a round waiting on a question: open and close only; open goes to its link", async () => {
    pending = [waiting];
    render(<AgentNotifications />);
    const banner = await screen.findByTestId("am-notification");
    expect(within(banner).getAllByRole("button").map((b) => b.getAttribute("aria-label") || b.textContent)).toEqual(["เปิด", "ปิด"]);
    fireEvent.click(within(banner).getByRole("button", { name: "เปิด" }));
    expect(nav.push).toHaveBeenCalledWith(LINK_WAIT);
    await waitFor(() => expect(callsTo("/agent-market/notifications/n-1/dismiss", "POST")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByTestId("am-notification")).toBeNull());
  });

  it("close dismisses it", async () => {
    pending = [waiting];
    render(<AgentNotifications />);
    fireEvent.click(within(await screen.findByTestId("am-notification")).getByRole("button", { name: "ปิด" }));
    await waitFor(() => expect(callsTo("/agent-market/notifications/n-1/dismiss", "POST")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByTestId("am-notification")).toBeNull());
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("a link that is not a page of this app is not followed", async () => {
    pending = [{ ...waiting, link: "https://pay-now.example/x" }];
    render(<AgentNotifications />);
    const banner = await screen.findByTestId("am-notification");
    expect(within(banner).queryByRole("button", { name: "เปิด" })).toBeNull();
    expect(within(banner).getByRole("button", { name: "ปิด" })).toBeTruthy();
  });

  it("raises a browser notification when the browser already allows it; a click focuses the tab and goes to the link", async () => {
    FakeNotification.permission = "granted";
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {});
    render(<AgentNotifications />);
    await screen.findByTestId("am-notification");
    await waitFor(() => expect(FakeNotification.made).toHaveLength(1));
    const n = FakeNotification.made[0]!;
    expect(n.title).toBe(due.title);
    expect(n.options?.body).toBe(due.body);
    n.onclick?.call(n, new Event("click"));
    expect(focus).toHaveBeenCalled();
    expect(nav.push).toHaveBeenCalledWith(LINK_DUE);
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
  });

  it("never asks for the permission, and raises nothing in the desktop app (its host shows its own)", async () => {
    render(<AgentNotifications />);
    await screen.findByTestId("am-notification");
    expect(FakeNotification.made).toHaveLength(0);
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    cleanup();
    hosted.now = true;
    FakeNotification.permission = "granted";
    pending = [due];
    render(<AgentNotifications />);
    await screen.findByTestId("am-notification");
    await waitFor(() => expect(callsTo("/agent-market/notifications/n-2/shown", "POST").length).toBeGreaterThan(0));
    expect(FakeNotification.made).toHaveLength(0);
  });
});

// ─────────────────────────── ?run=<id> ───────────────────────────

describe("AM6 — ?run=<id> brings that round into view", () => {
  const at = (s: number) => new Date(Date.UTC(2026, 9, 5, 2, 0, s)).toISOString();
  const item = (id: string, kind: string, payload: unknown, s: number, run?: string): ThreadItem =>
    ({ id, install_id: "i-1", kind, rev: s + 1, run_id: run, created_at: at(s), payload }) as ThreadItem;
  const items: ThreadItem[] = [
    item("g", "greeting", { text: "สวัสดีครับ" }, 0),
    item("p1", "progress", { run_id: "r-1", kind: "run", status: "succeeded", outcome: "completed" }, 1, "r-1"),
    item("o1", "outputs", { run_id: "r-1", period_label: "สิงหาคม 2569", artifacts: [], model: { uses: "none" } }, 2, "r-1"),
    item("p9", "progress", { run_id: "r-9", kind: "run", status: "awaiting_input", outcome: "awaiting_input" }, 3, "r-9"),
  ];
  const workspace: Handler = (path) => {
    if (path === "/agent-market/installs") return json({ installs: [] });
    if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", slug: "sran.financial-report", title: "ปิดงบ" }, manifest: { greeting: { role: "ผู้ช่วยปิดงบ" } }, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items, cursor: 9 });
    if (path.includes("/events")) return new Promise(() => {});
    if (path === "/agent-market/installs/i-1/models") return json({ uses: "none", choice: "auto", auto_model: "", models: [], blocked_reason: "", hidden_reason: "", reads: "", note: "" });
    if (path === "/agent-market/installs/i-1/storage")
      return json({ storage: { uploads: 0, outputs: 0, total: 0, warn_bytes: 1, warn: false }, retention_years: 5, retention: "", not_archive: "", personal_delete: "" });
    if (path === "/agent-market/installs/i-1/budget") return json({ budget: { tight: null }, remaining: 1 });
    if (path === "/agent-market/installs/i-1/estimate") return json({ basis: "none", basis_runs: 0, credits: null, thb: null, seconds: null, line: "" });
    if (path.startsWith("/agent-market/runs/") && path.endsWith("/exceptions")) return json({ exceptions: [] });
    if (path === "/agent-market/runs/r-5") return json({ run: { id: "r-5", install_id: "i-1", kind: "run", status: "awaiting_input", thread_id: "t-2", created_at: "" }, steps: [], clarifications: [], artifacts: [], files: [], children: [], install: { id: "i-1", title: "ปิดงบ", slug: "x" }, budget: { credits: 0, spent_this_month: 0 } });
    return json({ ok: true });
  };
  let scrolled: Element[];
  beforeEach(() => {
    handler = workspace;
    scrolled = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
      scrolled.push(this);
    });
  });

  it("from the address, once the thread has loaded", async () => {
    render(<AgentWorkspaceRoute params={{ installId: "i-1" }} searchParams={{ run: "r-9" }} />);
    await waitFor(() => expect(scrolled).toHaveLength(1));
    expect(scrolled[0]!.getAttribute("data-run")).toBe("r-9");
    expect(scrolled[0]!.getAttribute("data-testid")).toBe("am-progress");
    // once: the thread moving on does not pull the reader back
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(scrolled).toHaveLength(1);
  });

  it("a round of another chat with the agent: that chat is opened on it", async () => {
    render(<AgentWorkspaceRoute params={{ installId: "i-1" }} searchParams={{ run: "r-5" }} />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/chat/agents/i/i-1?thread=t-2&run=r-5"));
    expect(scrolled).toHaveLength(0);
  });

  it("no ?run: the thread lands at its end, as before", async () => {
    render(<AgentWorkspaceRoute params={{ installId: "i-1" }} />);
    await screen.findAllByTestId("am-progress");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(scrolled).toHaveLength(0);
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
