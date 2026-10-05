// AM10 F — the language a person chose is their account's: it persists across
// a reload, and across the browser and the desktop app for the same person.
//
//   * choosing here saves to the account (PATCH /portal/me);
//   * a client whose own choice the account already took follows the account
//     when the other client changed it;
//   * a choice made before any of this (or while the gateway was down) is
//     saved, never overwritten;
//   * a client that never chose takes the account's "th"; an account's "en"
//     is also what "never chose" looks like, so the browser's language stands.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/auth")>()), GATEWAY_URL: "http://gateway.test" }));

import { accountLanguageSeen, recheckAccountLanguage, resetAccountLanguageForTest, saveLanguage } from "./account";
import { useI18nStore } from "./index";

const LOCAL = "sran-chat-locale";
const SYNC = "sran-chat-locale-synced";
let fetchMock: ReturnType<typeof vi.fn>;
const patched = () =>
  fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PATCH").map((c) => JSON.parse(String((c[1] as RequestInit).body)));
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  window.localStorage.clear();
  resetAccountLanguageForTest();
  useI18nStore.setState({ locale: "en", hydrated: true });
  fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("a person's choice goes to their account", () => {
  it("the toggle saves it, and marks this client as holding the account's choice", async () => {
    useI18nStore.getState().setLocale("th");
    await flush();
    expect(fetchMock).toHaveBeenCalledWith("http://gateway.test/portal/me", expect.objectContaining({ method: "PATCH", credentials: "include" }));
    expect(patched()).toEqual([{ preferred_language: "th" }]);
    expect(window.localStorage.getItem(LOCAL)).toBe("th");
    expect(window.localStorage.getItem(SYNC)).toBe("1");
  });

  it("a save the gateway did not take stays pending, and is sent again at the next session check", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    useI18nStore.getState().setLocale("th");
    await flush();
    expect(window.localStorage.getItem(SYNC)).toBe("pending");
    // the account still says "en": this client's choice is not thrown away
    accountLanguageSeen("en");
    await flush();
    expect(useI18nStore.getState().locale).toBe("th");
    expect(patched()).toEqual([{ preferred_language: "th" }, { preferred_language: "th" }]);
    expect(window.localStorage.getItem(SYNC)).toBe("1");
  });

  it("a gateway that cannot be reached changes nothing on the page", async () => {
    fetchMock.mockRejectedValue(new Error("offline"));
    await expect(saveLanguage("th")).resolves.toBe(false);
    await expect(recheckAccountLanguage()).resolves.toBeUndefined();
    expect(useI18nStore.getState().locale).toBe("en");
  });
});

describe("the other client changed it", () => {
  it("a client that had saved its choice follows the account", () => {
    window.localStorage.setItem(LOCAL, "en");
    window.localStorage.setItem(SYNC, "1");
    accountLanguageSeen("th");
    expect(useI18nStore.getState().locale).toBe("th");
    expect(window.localStorage.getItem(LOCAL)).toBe("th");
    expect(document.documentElement.lang).toBe("th");
    expect(patched()).toEqual([]); // following is not choosing
  });

  it("coming back to the page asks the account again — at most once a minute", async () => {
    window.localStorage.setItem(LOCAL, "th");
    window.localStorage.setItem(SYNC, "1");
    useI18nStore.setState({ locale: "th" });
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ preferred_language: "en" }) });
    await recheckAccountLanguage(1_000_000);
    expect(useI18nStore.getState().locale).toBe("en");
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ preferred_language: "th" }) });
    await recheckAccountLanguage(1_030_000);
    expect(useI18nStore.getState().locale).toBe("en"); // within the minute: not asked
    await recheckAccountLanguage(1_061_000);
    expect(useI18nStore.getState().locale).toBe("th");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("a client meeting the account for the first time", () => {
  it("a choice made here before is saved to the account, not replaced by it", async () => {
    window.localStorage.setItem(LOCAL, "th");
    useI18nStore.setState({ locale: "th" });
    accountLanguageSeen("en");
    await flush();
    expect(useI18nStore.getState().locale).toBe("th");
    expect(patched()).toEqual([{ preferred_language: "th" }]);
    expect(window.localStorage.getItem(SYNC)).toBe("1");
  });

  it("the same choice as the account's needs no request", () => {
    window.localStorage.setItem(LOCAL, "en");
    accountLanguageSeen("en");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(SYNC)).toBe("1");
  });

  it("no choice here: the account's Thai is taken", () => {
    accountLanguageSeen("th");
    expect(useI18nStore.getState().locale).toBe("th");
    expect(window.localStorage.getItem(LOCAL)).toBe("th");
    expect(window.localStorage.getItem(SYNC)).toBe("1");
  });

  it("no choice here and the account says en (which a person who never chose also has): the browser's language stands", () => {
    useI18nStore.setState({ locale: "th" }); // a Thai browser, by navigator.language
    accountLanguageSeen("en");
    expect(useI18nStore.getState().locale).toBe("th");
    expect(window.localStorage.getItem(LOCAL)).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("anything that is not en or th is ignored", () => {
    accountLanguageSeen("ja");
    accountLanguageSeen(null);
    accountLanguageSeen(undefined);
    expect(useI18nStore.getState().locale).toBe("en");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
