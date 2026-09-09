// Shared unit-test setup: registers @testing-library/jest-dom's
// matchers (toBeInTheDocument, toHaveTextContent, …) on Vitest's
// expect for every test file.
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

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
