// AM11b part E — an agent that does what it is asked says so, and says what can be asked.
//
// A feature nothing mentions is a feature nobody uses: an agent took files and gave back documents, and a
// person had no way to know that "ไม่ต้องใส่กราฟ" typed with the files would be read. The greeting now
// names what can be asked of this agent's documents — its package's own list (manifest "instructions"),
// never an example made up on the screen — and that a request it cannot do is answered with the reason.
// An agent whose package declares nothing does not claim it listens: the line is not there.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/agents/i/i-1",
}));

import { useI18nStore } from "@/lib/i18n";

import { amDictionaries } from "../i18n";
import { stepLabel } from "../runs/RunDetailPage";
import type { ThreadItem } from "../types";
import { WorkspaceContext } from "./context";
import { ThreadView } from "./ThreadView";

const greeting: ThreadItem[] = [
  {
    id: "g-1",
    install_id: "i-1",
    kind: "greeting",
    payload: { text: "สวัสดีครับ ส่งไฟล์บัญชีของเดือนนั้นมาได้เลย", needs: [{ label: "บัญชีแยกประเภท" }], produces: [{ format: "docx", label: "รายงาน .docx" }] },
    rev: 1,
    created_at: "",
  } as unknown as ThreadItem,
];

function show(asks?: string[], fillsForms = false) {
  return render(
    <WorkspaceContext.Provider value={{ installId: "i-1", merge: vi.fn(), asks, fillsForms }}>
      <ThreadView items={greeting} />
    </WorkspaceContext.Provider>,
  );
}

describe("AM11b E — the greeting says what can be asked", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "th" });
    raw.mockResolvedValue({ ok: true, status: 200, text: async () => "{}" });
  });
  afterEach(cleanup);

  it("names the package's own directives, and that a refusal comes with its reason", () => {
    show(["ไม่ใส่กราฟ", "รายงานฉบับย่อ", "เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด"]);
    const line = screen.getByTestId("am-greeting-asks").textContent ?? "";
    expect(line).toContain("ไม่ใส่กราฟ · รายงานฉบับย่อ · เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด");
    expect(line).toContain("ข้อไหนทำไม่ได้จะบอกพร้อมเหตุผล");
  });

  it("is not there for an agent whose package declares nothing to ask — it does not claim to listen", () => {
    show(undefined, true);
    expect(screen.queryByTestId("am-greeting-asks")).toBeNull();
    expect(screen.getByTestId("am-greeting-form")).toBeTruthy(); // (the form line is its own)
    cleanup();
    show([]);
    expect(screen.queryByTestId("am-greeting-asks")).toBeNull();
  });

  it("is said in English on an English screen", () => {
    useI18nStore.setState({ locale: "en" });
    show(["No charts", "A short report"]);
    const line = screen.getByTestId("am-greeting-asks").textContent ?? "";
    expect(line).toBe("Tell it how you want the documents: No charts · A short report. What it cannot do, it says, with the reason.");
    expect(/[฀-๿]/.test(line)).toBe(false);
  });

  it("the step that reads the words has a name on the run page, in both languages", () => {
    const th = (k: string) => (amDictionaries.th as Record<string, string>)[k] ?? k;
    const en = (k: string) => (amDictionaries.en as Record<string, string>)[k] ?? k;
    expect(stepLabel("instructions", th as never)).toBe("อ่านคำขอที่พิมพ์มา");
    expect(stepLabel("instructions", en as never)).toBe("Read what was asked");
  });
});
