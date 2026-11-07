// FIX132 F15 — the sign-in page was the product's English front door:
// "Welcome back / Sign in with your company Google account." (NIGHT3
// j1-01), for a Thai product whose chat is Thai. The portal has no i18n
// layer, so the page is now written in Thai; brand and standard names
// (SRAN, Google, PDPA, AES-256, OpenAI) stay as they are.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));

beforeEach(() => {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.resetModules();
});

const ENGLISH = /Welcome back|Sign in with your company|Continue with Google|Back to home|Don.t have an account|Get started|Sign up free|Forgot password|Password|Email\b/;

async function mountForm(oauth: boolean) {
  vi.stubEnv("NEXT_PUBLIC_OAUTH_ENABLED", oauth ? "true" : "false");
  const { default: LoginForm } = await import("./_components/LoginForm");
  return render(<LoginForm />);
}

describe("FIX132 F15 — the sign-in page speaks Thai", () => {
  it("Google sign-in (the live setting): Thai heading, text, button and links", async () => {
    const { container } = await mountForm(true);
    expect(screen.getByRole("heading", { name: "ยินดีต้อนรับกลับมา" })).toBeTruthy();
    expect(container.textContent).toContain("เข้าสู่ระบบด้วยบัญชี Google ของบริษัท");
    expect(screen.getByRole("button", { name: "เข้าสู่ระบบด้วย Google" })).toHaveTextContent("ดำเนินการต่อด้วย Google");
    expect(screen.getByRole("link", { name: "กลับหน้าแรก" })).toBeTruthy();
    expect(container.textContent).not.toMatch(ENGLISH);
    expect(container.querySelector("[lang='th']")).not.toBeNull();
  });

  it("email + password form: Thai labels and button", async () => {
    const { container } = await mountForm(false);
    expect(screen.getByLabelText("อีเมล")).toBeTruthy();
    expect(screen.getByLabelText("รหัสผ่าน")).toBeTruthy();
    expect(screen.getByRole("button", { name: /เข้าสู่ระบบ/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: "ลืมรหัสผ่าน?" })).toBeTruthy();
    expect(container.textContent).not.toMatch(ENGLISH);
  });

  it("the page around the form: Thai title and panel", async () => {
    const page = await import("./page");
    expect(String(page.metadata.title)).toMatch(/เข้าสู่ระบบ/);
    const { container } = render(<page.default />);
    expect(container.textContent).not.toMatch(/Data protection|built in|Your AI infrastructure|running smoothly|Detectors|Providers/);
  });
});
