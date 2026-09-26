// AM2 — what the composer takes and says about photos.
//   * the file input's accept list is the manifest inputs' accept lists (as
//     the server checks them), AM1's list when an agent declares none;
//   * the agent's photo_notice shows under the chips while a photo (.jpg,
//     .jpeg, .png) is attached — never for a PDF, never for an agent
//     without a notice — and it does not block sending.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { upload } = vi.hoisted(() => ({ upload: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("../api", async (importOriginal) => {
  const m = await importOriginal<typeof import("../api")>();
  return { ...m, amApi: { ...m.amApi, upload } };
});

import { useI18nStore } from "@/lib/i18n";

import type { Manifest } from "../types";
import { Composer } from "./Composer";
import { acceptFromManifest, DEFAULT_ACCEPT, hasImage, isImageName } from "./useUploads";

const NOTICE = "รูปถ่ายใบเสร็จอ่านแบบพยายามที่สุด (best-effort): อ่านเฉพาะผู้ขาย วันที่ ยอดรวม VAT และเลขผู้เสียภาษี";
const invoice: Manifest = {
  inputs: [{ role: "documents", label: "ใบกำกับภาษี", accept: [".pdf", ".xml", ".jpg", ".jpeg", ".png"] }],
  photo_notice: NOTICE,
};
const financial: Manifest = {
  inputs: [
    { role: "primary", label: "บัญชีแยกประเภท", accept: [".xlsx", ".csv"] },
    { role: "budget", label: "งบประมาณ", accept: [".xlsx", ".csv"] },
    { role: "prior", label: "เดือนก่อนหน้า", accept: [".xlsx", ".csv"] },
  ],
};

beforeEach(() => {
  upload.mockReset();
  upload.mockImplementation(() => new Promise(() => {})); // stays "uploading"
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

const file = (name: string) => new File(["x"], name);
const attach = (...names: string[]) => fireEvent.change(screen.getByTestId("am-composer-file"), { target: { files: names.map(file) } });

describe("acceptFromManifest", () => {
  it("is the inputs' accept lists, lower-cased and deduped", () => {
    expect(acceptFromManifest(invoice)).toEqual([".pdf", ".xml", ".jpg", ".jpeg", ".png"]);
    expect(acceptFromManifest(financial)).toEqual([".xlsx", ".csv"]);
    expect(acceptFromManifest({ inputs: [{ role: "a", label: "a", accept: [".PDF", " .Xml ", "pdf"] }] })).toEqual([".pdf", ".xml"]);
  });

  it("falls back to AM1's list when no input declares one", () => {
    expect(acceptFromManifest({})).toEqual(DEFAULT_ACCEPT);
    expect(acceptFromManifest({ inputs: [{ role: "a", label: "a" }] })).toEqual(DEFAULT_ACCEPT);
    expect(acceptFromManifest(null)).toEqual(DEFAULT_ACCEPT);
  });
});

describe("hasImage", () => {
  it("is true for a staged photo only (a refused one is not attached)", () => {
    expect(isImageName("receipt.JPG")).toBe(true);
    expect(isImageName("scan.jpeg")).toBe(true);
    expect(isImageName("scan.png")).toBe(true);
    expect(isImageName("invoice.pdf")).toBe(false);
    expect(isImageName("etax.xml")).toBe(false);
    expect(hasImage([{ name: "invoice.pdf", status: "done" }])).toBe(false);
    expect(hasImage([{ name: "invoice.pdf", status: "done" }, { name: "r.png", status: "uploading" }])).toBe(true);
    expect(hasImage([{ name: "r.png", status: "error" }])).toBe(false);
  });
});

describe("Composer", () => {
  it("the file input accepts what the manifest says", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={acceptFromManifest(invoice)} photoNotice={NOTICE} />);
    expect(screen.getByTestId("am-composer-file").getAttribute("accept")).toBe(".pdf,.xml,.jpg,.jpeg,.png");
  });

  it("the photo notice shows only while a photo is attached, and does not block sending", async () => {
    upload.mockImplementation(async (_installId: string, f: File) => ({ id: `f-${f.name}`, name: f.name, size: f.size }));
    render(<Composer installId="i-1" onSent={vi.fn()} accept={acceptFromManifest(invoice)} photoNotice={NOTICE} />);
    attach("IV6909-0015.pdf", "etax.xml");
    expect(screen.getAllByTestId("am-staged-chip")).toHaveLength(2);
    expect(screen.queryByTestId("am-photo-notice")).toBeNull();

    attach("receipt.JPG");
    expect(screen.getByTestId("am-photo-notice").textContent).toBe(NOTICE);
    const send = screen.getByTestId("am-composer-send") as HTMLButtonElement;
    await waitFor(() => expect(send.disabled).toBe(false));
    expect(screen.getByTestId("am-photo-notice")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "เอาไฟล์ receipt.JPG ออก" }));
    expect(screen.queryByTestId("am-photo-notice")).toBeNull();
  });

  it("no notice for an agent without one (the financial report)", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx", ".csv", ".png"]} />);
    attach("chart.png");
    expect(screen.getAllByTestId("am-staged-chip")).toHaveLength(1);
    expect(screen.queryByTestId("am-photo-notice")).toBeNull();
  });

  it("a photo the agent does not take is refused, and gets no notice", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={acceptFromManifest(financial)} photoNotice={NOTICE} />);
    attach("receipt.jpg");
    expect(screen.getByTestId("am-staged-chip").getAttribute("data-status")).toBe("error");
    expect(screen.queryByTestId("am-photo-notice")).toBeNull();
    expect(upload).not.toHaveBeenCalled();
  });
});
