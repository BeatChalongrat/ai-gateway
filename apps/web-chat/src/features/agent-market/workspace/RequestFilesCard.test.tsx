// AM1 — the request-files card: what is asked for, an upload slot, and the
// options as buttons EXCEPT "wait" (uploading is what waiting means).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import type { CardPayload } from "../types";
import { RequestFilesCard } from "./RequestFilesCard";

const card: CardPayload = {
  card_id: "card-9",
  run_id: "run-9",
  kind: "request_files",
  status: "open",
  blocking: true,
  attempts: 0,
  questions: [
    {
      key: "missing_file",
      code: "missing_file",
      severity: "blocking",
      header: "ขาดไฟล์",
      question: "คุณขอให้เทียบกับงบประมาณ แต่ยังไม่มีไฟล์งบประมาณครับ",
      options: [
        { label: "ไม่มีไฟล์นี้ ทำเท่าที่มี", description: "รายงานจะระบุว่าไม่ได้เทียบกับงบประมาณ", value: "proceed_without" },
        { label: "รอก่อน จะอัปโหลดให้", description: "ผมจะรอไฟล์ แล้วทำต่อจากจุดนี้", value: "wait" },
        { label: "ยกเลิกรอบนี้", description: "ปิดรอบนี้ไว้ก่อน", value: "cancel" },
      ],
      default: "wait",
      default_label: "รอก่อน จะอัปโหลดให้",
      request: [{ role: "budget", label: "ไฟล์งบประมาณของงวดนี้", why: "ใช้เทียบค่าใช้จ่ายจริงกับงบ", required: false, accept: [".xlsx", ".csv"] }],
    },
  ],
};

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("RequestFilesCard", () => {
  it("lists the requested file and hides the 'wait' option", () => {
    render(<RequestFilesCard card={card} submit={vi.fn()} />);
    const root = screen.getByTestId("am-request-files");
    expect(root.textContent).toContain("ไฟล์งบประมาณของงวดนี้");
    expect(root.textContent).toContain("ใช้เทียบค่าใช้จ่ายจริงกับงบ");
    expect(root.textContent).toContain("รับไฟล์ .xlsx .csv");
    const options = screen.getByTestId("am-request-options");
    const labels = [...options.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["ไม่มีไฟล์นี้ ทำเท่าที่มี", "ยกเลิกรอบนี้"]);
    expect(screen.queryByText("รอก่อน จะอัปโหลดให้")).toBeNull();
  });

  it("an option answers the card with that label", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<RequestFilesCard card={card} submit={submit} />);
    fireEvent.click(screen.getByText("ไม่มีไฟล์นี้ ทำเท่าที่มี"));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith({ answers: { missing_file: { labels: ["ไม่มีไฟล์นี้ ทำเท่าที่มี"] } } });
  });

  it("a closed request-files card is a summary with no buttons", () => {
    render(<RequestFilesCard card={{ ...card, status: "answered", answered_via: "upload", answer: { upload: {} } }} submit={vi.fn()} />);
    expect(screen.getByTestId("am-card-answered").textContent).toContain("ได้รับไฟล์แล้ว");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
