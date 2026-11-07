// Phase-4 projects render tests — the projects page under both
// locales, asserting the dict strings land (and don't leak English
// in Thai). The typed Record<DictKey, string> already guarantees
// EN/TH key parity at compile time.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    listProjects: vi.fn(),
    getPreferences: vi.fn(),
    putPreferences: vi.fn(),
    listConversationsPage: vi.fn(),
    listKnowledgeBases: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import ProjectsPage from "@/app/chat/projects/page";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "@/stores/chatStore";

const initialChatState = useChatStore.getState();

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no matchMedia; the (closed) create modal's viewport
  // hook still calls it on mount.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
  useI18nStore.setState({ locale: "en" });
  // Pre-seed the stores as already loaded (empty) so the page's mount
  // effects skip the fetch loaders; the personal-instructions panel
  // still loads preferences, which we resolve to empty strings.
  useChatStore.setState(
    {
      ...initialChatState,
      projects: [],
      projectsLoaded: true,
      projectsError: null,
      conversationsLoaded: true,
    },
    true,
  );
  mockApi.getPreferences.mockResolvedValue({
    custom_instructions_about: "",
    custom_instructions_respond: "",
  });
});

describe("projects page locale rendering", () => {
  it("renders the EN title, empty state and personal panel in en", async () => {
    render(<ProjectsPage />);
    expect(
      screen.getByText(dictionaries.en["projects.title"]),
    ).toBeInTheDocument();
    expect(
      screen.getByText(dictionaries.en["projects.empty.title"]),
    ).toBeInTheDocument();
    expect(
      screen.getByText(dictionaries.en["projects.personal.title"]),
    ).toBeInTheDocument();
    // Preferences resolve → the About-you field appears.
    expect(
      await screen.findByText(dictionaries.en["projects.personal.about.label"]),
    ).toBeInTheDocument();
  });

  it("renders the TH strings in th with no English leak", async () => {
    useI18nStore.setState({ locale: "th" });
    render(<ProjectsPage />);
    expect(
      screen.getByText(dictionaries.th["projects.title"]),
    ).toBeInTheDocument();
    expect(
      screen.getByText(dictionaries.th["projects.empty.title"]),
    ).toBeInTheDocument();
    expect(
      screen.getByText(dictionaries.th["projects.personal.title"]),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(dictionaries.th["projects.personal.about.label"]),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(dictionaries.en["projects.title"]),
    ).toBeNull();
    expect(
      screen.queryByText(dictionaries.en["projects.empty.title"]),
    ).toBeNull();
  });
});
