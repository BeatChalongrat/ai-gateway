// Parity + hygiene guard for the admin console dictionary.
//
// The PRIMARY parity mechanism is the type system: every area module types
// its Thai export as `Record<keyof typeof en, string>`, so a missing key
// fails the build. These tests are the second belt — they catch the things
// a type cannot:
//   * an `as any` / `as Record<string, string>` escape hatch,
//   * a key added to the composed dictionary but not to any module,
//   * an English string pasted into the Thai side and never translated,
//   * duplicate keys across modules silently overwriting each other.

import { describe, expect, it } from "vitest";

import { dictionaries, en, modules, th } from "./dict";

describe("admin-ui dictionary", () => {
  it("has exactly the same key set in English and Thai", () => {
    const enKeys = Object.keys(en).sort();
    const thKeys = Object.keys(th).sort();
    expect(thKeys).toEqual(enKeys);
  });

  it("has every key filled in both languages", () => {
    for (const [key, value] of Object.entries(en)) {
      // Stub placeholders exist only while a module is still being filled
      // in; they carry no UI text and are excluded rather than blocking.
      if (key.endsWith("_placeholder")) continue;
      expect(value, `en.${key} is empty`).toBeTruthy();
    }
    for (const [key, value] of Object.entries(th)) {
      if (key.endsWith("_placeholder")) continue;
      expect(value, `th.${key} is empty`).toBeTruthy();
    }
  });

  it("keeps each area module internally in parity", () => {
    for (const [name, mod] of Object.entries(modules)) {
      const enKeys = Object.keys(mod.en).sort();
      const thKeys = Object.keys(mod.th).sort();
      expect(thKeys, `module '${name}' is out of parity`).toEqual(enKeys);
    }
  });

  it("does not let two modules claim the same key", () => {
    const seen = new Map<string, string>();
    for (const [name, mod] of Object.entries(modules)) {
      for (const key of Object.keys(mod.en)) {
        const owner = seen.get(key);
        expect(
          owner,
          `key '${key}' is defined in both '${owner}' and '${name}' — the later module silently wins`,
        ).toBeUndefined();
        seen.set(key, name);
      }
    }
  });

  it("does not ship untranslated Thai values", () => {
    // A Thai value identical to its English one is almost always a
    // copy-paste that never got translated. The exceptions are strings
    // that are SUPPOSED to be identical: brand names, language names in
    // their own script, and format-only strings.
    const intentionallyIdentical = new Set<string>([
      "app.title",
      "app.languageEn",
      "app.languageTh",
      "nav.apiKeys",
    ]);

    const untranslated = Object.keys(en).filter((key) => {
      const k = key as keyof typeof en;
      return (
        !intentionallyIdentical.has(key) &&
        th[k] === en[k] &&
        // A pure placeholder/number string has nothing to translate.
        /\p{Letter}/u.test(en[k])
      );
    });

    expect(untranslated, "these Thai values are still the English text").toEqual([]);
  });

  it("uses the same placeholders on both sides of every key", () => {
    const placeholders = (s: string) =>
      (s.match(/\{(\w+)\}/g) ?? []).sort().join(",");

    for (const key of Object.keys(en)) {
      const k = key as keyof typeof en;
      expect(placeholders(th[k]), `placeholder mismatch in '${key}'`).toBe(
        placeholders(en[k]),
      );
    }
  });

  it("exposes both locales through the dictionaries map", () => {
    expect(Object.keys(dictionaries).sort()).toEqual(["en", "th"]);
  });
});
