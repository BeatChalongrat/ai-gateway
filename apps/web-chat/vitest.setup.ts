// Shared unit-test setup: registers @testing-library/jest-dom's
// matchers (toBeInTheDocument, toHaveTextContent, …) on Vitest's
// expect for every test file.
import "@testing-library/jest-dom/vitest";
import { beforeEach, expect, vi } from "vitest";

// AM11 — Agent Market follows the app's language, and the app's store starts
// in English. The feature's tests were written against the Thai dictionary
// and assert its words, so every test under src/features/agent-market/
// starts with the app in Thai; a test of the English side sets "en" itself
// (its own beforeEach runs after this one). The store is imported here, when
// the test starts, and not at the top of this file: a module a setup file
// imports is loaded before the test file's vi.mock calls and would be handed
// to every test in the app unmocked.
beforeEach(async () => {
  const file = (expect.getState().testPath ?? "").replace(/\\/g, "/");
  if (!file.includes("/src/features/agent-market/")) return;
  const { useI18nStore } = await import("@/lib/i18n");
  useI18nStore.setState({ locale: "th", hydrated: true });
});

// FIX89 — Next's app-router hooks throw "invariant expected app router to be
// mounted" outside an <AppRouterContext>, which every jsdom test is. A
// component that can navigate (the composer's + menu opens the connectors
// page) would otherwise be unmountable in any test that does not care about
// routing. This default is inert: a test that ASSERTS navigation declares its
// own `vi.mock("next/navigation", …)`, which takes precedence over a setup
// file's.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/chat",
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
  useSelectedLayoutSegments: () => [],
  redirect: vi.fn(),
  notFound: vi.fn(),
}));
