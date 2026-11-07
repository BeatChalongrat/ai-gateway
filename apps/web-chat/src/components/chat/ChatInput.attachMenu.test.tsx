// FIX89 Part B — connectors are reachable from the composer, the way Claude
// reaches its connector directory from the "+".
//
// On disk the "+" was not a menu at all: one click, straight to the file
// picker. It becomes a menu only on the DESKTOP, where the connectors page
// exists; in a plain browser /chat/connectors bounces back to /chat
// (useDesktopOnlyRoute), so there the "+" keeps its single click.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, nav, hostedState } = vi.hoisted(() => ({
  mockApi: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), getFileText: vi.fn() },
  nav: { push: vi.fn() },
  hostedState: { hosted: true },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("next/navigation", () => ({ useRouter: () => nav, useParams: () => ({}), usePathname: () => "/chat" }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hostedState.hosted }));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isSupported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isSupported: false, isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  hostedState.hosted = true;
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial, activeConversationId: "c1" }, true);
  useModelStore.setState({ models: [], selectedModelId: AUTO_MODEL_ID });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
afterEach(cleanup);

function mount() {
  return render(
    <ChatInput
      conversationId="c1"
      disabled={false}
      streaming={false}
      activeModelId={AUTO_MODEL_ID}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );
}

describe("the composer's + on the desktop", () => {
  it("opens a menu with Attach files and Connectors", () => {
    mount();
    expect(screen.queryByTestId("attach-menu")).toBeNull();
    fireEvent.click(screen.getByTestId("attach-button"));
    const menu = screen.getByTestId("attach-menu");
    expect(menu.getAttribute("role")).toBe("menu");
    expect(screen.getByTestId("attach-menu-files").textContent).toContain("Attach files");
    expect(screen.getByTestId("attach-menu-connectors").textContent).toContain("Connectors");
    expect(screen.getByTestId("attach-button").getAttribute("aria-expanded")).toBe("true");
  });

  it("Connectors goes to the page that already exists", () => {
    mount();
    fireEvent.click(screen.getByTestId("attach-button"));
    fireEvent.click(screen.getByTestId("attach-menu-connectors"));
    expect(nav.push).toHaveBeenCalledWith("/chat/connectors");
    expect(screen.queryByTestId("attach-menu")).toBeNull(); // and it closes
  });

  it("Attach files still opens the file picker", () => {
    const { container } = mount();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByTestId("attach-button"));
    fireEvent.click(screen.getByTestId("attach-menu-files"));
    expect(click).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("attach-menu")).toBeNull();
  });

  it("Escape closes it (the one dismiss rule, FIX84)", () => {
    mount();
    fireEvent.click(screen.getByTestId("attach-button"));
    expect(screen.getByTestId("attach-menu")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("attach-menu")).toBeNull();
  });

  it("localises to Thai", () => {
    useI18nStore.setState({ locale: "th" });
    mount();
    fireEvent.click(screen.getByTestId("attach-button"));
    expect(screen.getByTestId("attach-menu-connectors").textContent).toContain("ตัวเชื่อมต่อ");
  });
});

describe("the composer's + on the web", () => {
  it("has no menu — one click, straight to the file picker (there is no connectors page here)", () => {
    hostedState.hosted = false;
    const { container } = mount();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByTestId("attach-button"));
    expect(screen.queryByTestId("attach-menu")).toBeNull();
    expect(click).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("attach-button").getAttribute("aria-haspopup")).toBeNull();
  });
});
