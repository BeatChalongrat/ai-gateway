// AM6 — the un-fork, finished.
//
// AM5 made the workspace render the chat's composer, bubbles, file cards,
// question card and model menu. What stayed a copy was the feature's small
// kit: five button class strings (BTN_*), a spinner (Spin) and a dialog
// (AmDialog), beside the app's Button, Spinner and Modal. The same "save"
// was one button on the settings page and another in an agent's settings.
//
// The first tests read the feature's sources: the old names are gone, and
// nothing in the feature draws a spinner or a dialog of its own. The others
// render a screen of the feature beside the app's component and compare what
// a person sees — the same element with the same classes, because it is the
// same component (as am5_shared.test.tsx does for the composer and the thread).
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import type { ComponentProps } from "react";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  usePathname: () => "/chat/agents",
}));

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { useI18nStore } from "@/lib/i18n";

import { DashboardPage } from "./dashboard/DashboardPage";
import { tAm } from "./i18n";
import { QuickInstall } from "./install/QuickInstall";
import { SignOffButton } from "./review/SignOff";
import { ShareButton } from "./share/ShareButton";
import { ErrorPanel, NotAvailable } from "./ui";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { EmptyDrop } from "./workspace/EmptyDrop";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) =>
    path === "/agent-market/availability" ? json({ available: true, runner: { healthy: true } }) : handler(path, init),
  );
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** What an element looks like: its classes, whatever the spacing between them. */
const look = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").replace(/\s+/g, " ").trim();

/** The app's Button rendered alone: what a button of that kind looks like. */
function appButton(props: ComponentProps<typeof Button> = {}): string {
  const app = render(<Button {...props}>x</Button>);
  const want = look(app.container.querySelector("button"));
  app.unmount();
  return want;
}

// ─────────────────────────── the sources ───────────────────────────

const FEATURE = resolve(__dirname);

function sources(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) sources(path, acc);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) acc.push(path);
  }
  return acc;
}

/** Every place in the feature's sources where the pattern is written, as "file: what". */
function written(pattern: RegExp): string[] {
  const found: string[] = [];
  for (const file of sources(FEATURE)) {
    const names = new Set(readFileSync(file, "utf8").match(pattern) ?? []);
    for (const name of names) found.push(`${relative(FEATURE, file).replace(/\\/g, "/")}: ${name}`);
  }
  return found.sort();
}

describe("AM6 — the feature has no kit of its own", () => {
  it("no file exports or imports the old button classes, the old spinner or the old dialog", () => {
    expect(written(/\b(?:BTN_[A-Z_]+|Spin|AmDialog)\b/g)).toEqual([]);
  });

  it("dialog.tsx is gone", () => {
    expect(existsSync(join(FEATURE, "dialog.tsx"))).toBe(false);
  });

  it("nothing in the feature draws a spinner or a dialog itself", () => {
    expect(written(/animate-spin|role="dialog"|aria-modal/g)).toEqual([]);
  });
});

// ─────────────────────────── buttons ───────────────────────────

describe("AM6 — a button in Agent Market is the app's Button", () => {
  it("the main action", () => {
    render(<EmptyDrop manifest={null} onPick={vi.fn()} />);
    expect(look(screen.getByTestId("am-empty-pick"))).toBe(appButton());
  });

  it("a small action beside other things", () => {
    render(<ShareButton runId="r-1" />);
    expect(look(screen.getByTestId("am-share-open"))).toBe(appButton({ variant: "secondary", size: "sm" }));
  });

  it("a link that looks like a button is still a link, in the Button's classes", () => {
    render(<QuickInstall slug="sran.financial-report" />);
    const link = screen.getByTestId("am-detail-install-custom");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/chat/agents/install/sran.financial-report");
    expect(look(link)).toBe(appButton({ variant: "secondary" }));
  });

  it("the way back from a page that is not available", () => {
    render(<NotAvailable />);
    const link = within(screen.getByTestId("am-not-available")).getByRole("link", { name: tAm("gate.backToChat") });
    expect(link.getAttribute("href")).toBe("/chat");
    expect(look(link)).toBe(appButton({ variant: "secondary", className: "mt-6" }));
  });

  it("the retry of a load that failed", () => {
    const onRetry = vi.fn();
    render(<ErrorPanel message="โหลดไม่สำเร็จ" onRetry={onRetry} />);
    const retry = within(screen.getByRole("alert")).getByRole("button", { name: tAm("common.retry") });
    expect(look(retry)).toBe(appButton({ variant: "secondary", size: "sm", className: "shrink-0" }));
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

// ─────────────────────────── waiting ───────────────────────────

describe("AM6 — a wait in Agent Market is the app's", () => {
  it("a list that is loading shows the app's Spinner", async () => {
    // AM7: the admins' list of runs moved to the admin app; the agent's dashboard, loading another range, waits here
    const dash = {
      since: "2026-08-30T00:00:00Z", days: 30, version: "1.0.0",
      runs: { total: 1, by_status: { succeeded: 1 }, succeeded: 1, first_pass: 1, first_pass_rate: 1 },
      time: { queue_p50_s: 4, queue_p90_s: 12, work_p50_s: 95, work_p90_s: 400, human_wait_p50_s: null, human_wait_p90_s: null },
      waiting: { runs: 0, oldest_run_s: null, open_exceptions: 0, oldest_exception_s: null },
      questions: [], exceptions: { open: 0, resolved: 0, noted: 0, by_reason: [], choices: [] }, failures: [], confidence: {}, tiers: {},
      documents: 0, artifacts: [], versions: [], spend: { credits: 300, per_succeeded_run: 300, per_document: null, budget_credits: 50000 }, models: [],
    };
    let hang = false;
    handler = (path) => (hang ? new Promise(() => {}) : path.startsWith("/agent-market/installs/i-1/dashboard") ? json(dash) : json({}, 404));
    render(<DashboardPage installId="i-1" />);
    const range = await screen.findByTestId("am-dash-range");
    hang = true;
    fireEvent.click(within(range).getAllByRole("button")[0]!);
    const wait = range.parentElement?.lastElementChild;

    const app = render(<Spinner size={14} />);
    const want = app.container.firstElementChild;
    expect({ tag: wait?.tagName, look: look(wait), style: wait?.getAttribute("style") }).toEqual({
      tag: want?.tagName,
      look: look(want),
      style: want?.getAttribute("style"),
    });
  });

  it("a button that is working is the app's Button, loading", () => {
    const app = render(<Button loading>x</Button>);
    const want = look(app.container.querySelector("button")?.firstElementChild);
    app.unmount();

    handler = () => new Promise(() => {});
    render(<QuickInstall slug="sran.financial-report" />);
    const install = screen.getByTestId("am-detail-install") as HTMLButtonElement;
    fireEvent.click(install);
    expect(install.disabled).toBe(true);
    expect(install.firstElementChild?.tagName).toBe("SPAN");
    expect(look(install.firstElementChild)).toBe(want);
    expect(install.textContent).toBe(tAm("install.recommending"));
  });
});

// ─────────────────────────── dialogs ───────────────────────────

/** What a dialog looks like: the backdrop, the card, its header and the close button. */
function dialogLook(dialog: HTMLElement) {
  const card = dialog.firstElementChild;
  const header = dialog.querySelector("header");
  const close = header?.querySelector("button");
  return {
    role: dialog.getAttribute("role"),
    modal: dialog.getAttribute("aria-modal"),
    backdrop: look(dialog),
    card: look(card),
    cardStyle: card?.getAttribute("style"),
    header: look(header),
    title: look(header?.querySelector("h2")),
    description: look(header?.querySelector("p")),
    close: look(close),
    closeLabel: close?.getAttribute("aria-label"),
    body: look(header?.nextElementSibling),
  };
}

describe("AM6 — a dialog in Agent Market is the app's Modal", () => {
  it("sign-off opens the app's dialog, under the same test id", () => {
    const app = render(
      <Modal open onClose={() => {}} title={tAm("review.title")} description={tAm("review.body")}>
        x
      </Modal>,
    );
    const want = dialogLook(app.getByRole("dialog"));
    app.unmount();

    render(<SignOffButton runId="r-1" />);
    expect(screen.queryByTestId("am-signoff-dialog")).toBeNull();
    fireEvent.click(screen.getByTestId("am-signoff-open"));
    const dialog = screen.getByTestId("am-signoff-dialog");
    expect(dialogLook(dialog)).toEqual(want);
    expect(dialog.getAttribute("aria-label")).toBe(tAm("review.title"));
    expect(dialog.textContent).toContain(tAm("review.body"));
    expect(within(dialog).getByTestId("am-signoff-send")).toBeTruthy();
  });

  it("reads in Thai inside an English app, and closes", () => {
    useI18nStore.setState({ locale: "en" });
    render(<SignOffButton runId="r-1" />);
    fireEvent.click(screen.getByTestId("am-signoff-open"));
    fireEvent.click(within(screen.getByTestId("am-signoff-dialog")).getByRole("button", { name: "ปิด" }));
    expect(screen.queryByTestId("am-signoff-dialog")).toBeNull();
  });

  it("Escape closes it", () => {
    render(<ShareButton runId="r-1" />);
    fireEvent.click(screen.getByTestId("am-share-open"));
    expect(screen.getByTestId("am-share-dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("am-share-dialog")).toBeNull();
  });
});
