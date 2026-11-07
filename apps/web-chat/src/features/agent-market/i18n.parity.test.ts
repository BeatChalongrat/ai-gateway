// AM11 — parity and hygiene of Agent Market's two dictionaries.
//
// A string exists in Thai and in English or it does not exist
// (docs/agent-market/GLOSSARY.md §6 rule 1). The first belt is the type
// system: each part's English object is checked against its Thai twin, and
// the merged dictionaries against each other, so a key in one language only
// fails tsc. These tests are the second belt — what a type cannot see:
//   * a cast (`as any`, `as Record<string, string>`) that hides a key,
//   * a key claimed by two parts (the later spread silently wins),
//   * an empty value, a placeholder that one side lost or renamed,
//   * Thai left sitting in an English value, an English value that is still
//     the Thai text,
//   * a word the glossary settled, said another way,
//   * a sentence that reads like an error (GLOSSARY.md §6 rule 4).
// Modelled on admin-ui/lib/i18n/dict.test.ts; `npm run lint:i18n` runs the
// key check too, so a build stops on it without running the tests.

import { describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";

import { amDictionaries, amParts, tAm, type AmKey } from "./i18n";

const th: Record<string, string> = amDictionaries.th;
const en: Record<string, string> = amDictionaries.en;

const THAI = /[฀-๿]/;
const placeholders = (s: string) => [...new Set(s.match(/\{(\w+)\}/g) ?? [])].sort().join(",");
/** Keys of `a` that `b` does not have. */
const missingFrom = (a: Record<string, string>, b: Record<string, string>) => Object.keys(a).filter((k) => !(k in b)).sort();

describe("Agent Market dictionaries — the same keys in Thai and in English", () => {
  it("both languages are there, and only those two", () => {
    expect(Object.keys(amDictionaries).sort()).toEqual(["en", "th"]);
  });

  it("every Thai key has its English, and every English key its Thai", () => {
    expect(missingFrom(th, en), "in Thai only — write the English").toEqual([]);
    expect(missingFrom(en, th), "in English only — write the Thai").toEqual([]);
    expect(Object.keys(en).length).toBe(Object.keys(th).length);
  });

  it("each part is in parity by itself, so a mismatch names its file", () => {
    for (const [file, part] of Object.entries(amParts)) {
      expect(missingFrom(part.th, part.en), `${file}: in Thai only`).toEqual([]);
      expect(missingFrom(part.en, part.th), `${file}: in English only`).toEqual([]);
    }
  });

  it("no key is claimed by two parts, and the parts make up the whole dictionary", () => {
    const owner = new Map<string, string>();
    for (const [file, part] of Object.entries(amParts)) {
      for (const key of Object.keys(part.th)) {
        expect(owner.get(key), `'${key}' is in both ${owner.get(key)} and ${file} — the later one silently wins`).toBeUndefined();
        owner.set(key, file);
      }
    }
    expect([...owner.keys()].sort()).toEqual(Object.keys(th).sort());
  });

  it("every value is filled, in both languages", () => {
    for (const [key, value] of Object.entries(th)) expect(value.trim(), `th '${key}' is empty`).not.toBe("");
    for (const [key, value] of Object.entries(en)) expect(value.trim(), `en '${key}' is empty`).not.toBe("");
  });

  it("the same {placeholders} on both sides of every key", () => {
    const drift = Object.keys(th)
      .filter((key) => placeholders(th[key]!) !== placeholders(en[key] ?? ""))
      .map((key) => `${key}: th {${placeholders(th[key]!)}} / en {${placeholders(en[key] ?? "")}}`);
    expect(drift).toEqual([]);
  });

  it("no Thai letter in any English value", () => {
    const thai = Object.entries(en)
      .filter(([, value]) => THAI.test(value))
      .map(([key, value]) => `${key}: ${value}`);
    expect(thai).toEqual([]);
  });

  it("no English value is still the Thai text", () => {
    // What reads the same in both languages, each with its reason (GLOSSARY.md §5):
    // names, file formats and version marks, and values that are punctuation only.
    const same: Record<string, string> = {
      "common.sep": "a separator: punctuation only",
      "rail.market": "the product's name",
      "market.title": "the product's name",
      "detail.back": "the product's name (the link back to it)",
      "evidence.raw": "quotation marks around the person's own text",
      "exq.raw": "quotation marks around the person's own text",
      "outputs.version": "a version mark (v2)",
      "outputs.fmt.xlsx": "a file format",
      "outputs.fmt.docx": "a file format",
      "outputs.fmt.pptx": "a file format",
      "settings.mappingPair": "two names and an arrow",
      "dash.tier.etax": "a standard's name and a file format",
      "receipt.lineNoNumber": "PO is said as PO in both languages",
    };
    const untranslated = Object.keys(th).filter((key) => en[key] === th[key] && !(key in same));
    expect(untranslated, "these English values are identical to the Thai ones").toEqual([]);
    // the list holds nothing that is no longer identical (a stale entry would hide the next copy-paste)
    expect(Object.keys(same).filter((key) => en[key] !== th[key])).toEqual([]);
  });
});

describe("Agent Market dictionaries — the settled words (GLOSSARY.md)", () => {
  // Where the Thai says the term, the English says the glossary's word for it.
  // An exception is a Thai string that holds the letters but not the term.
  const TERMS: Array<{ thai: RegExp; english: RegExp; word: string; except?: Record<string, string> }> = [
    { thai: /รอบ/, english: /\bruns?\b/i, word: "run", except: { "tpl.map.proposedBy": "รอบช่อง is “around the field”, not a run" } },
    { thai: /งวด/, english: /\bperiod\b/i, word: "period" },
    { thai: /ที่มาของตัวเลข/, english: /source of figures/i, word: "source of figures" },
    { thai: /ข้อสมมติที่ใช้/, english: /assumptions used/i, word: "assumptions used" },
    { thai: /ลงตัว/, english: /\breconciled\b/i, word: "reconciled · not reconciled" },
    { thai: /รายการกระทบยอด/, english: /reconciling items/i, word: "reconciling items" },
    { thai: /ติดเงื่อนไข/, english: /\bblocked\b/i, word: "blocked" },
    { thai: /ต้องให้คนตัดสิน/, english: /needs a decision/i, word: "needs a decision" },
    { thai: /พร้อมเสนออนุมัติ/, english: /ready to submit for approval/i, word: "ready to submit for approval" },
    { thai: /ร่าง/, english: /\bdraft/i, word: "draft" },
    { thai: /ห้องทำงาน/, english: /\bworkspace\b/i, word: "workspace" },
    { thai: /ตรวจทานและลงชื่อ/, english: /review(ed)? and sign(ed)?( it)? off/i, word: "review and sign off" },
    { thai: /ค่าตั้งต้น/, english: /\bdefaults?\b/i, word: "default" },
    { thai: /ค่าแนะนำ/, english: /\brecommended\b/i, word: "recommended" },
    { thai: /ผู้ดูแล/, english: /\badmins?\b/i, word: "admin" },
    { thai: /ตรวจ 3 ทาง/, english: /3-way match/i, word: "3-way match" },
    { thai: /ตรวจ 2 ทาง/, english: /2-way match/i, word: "2-way match" },
    { thai: /จับคู่(คอลัมน์|ช่อง)/, english: /\bmap(ped|ping)?\b/i, word: "map (columns, fields)" },
    { thai: /จับคู่/, english: /\b(map(ped|ping)?|match(ed|es)?)\b/i, word: "map or match" },
    { thai: /ใบกำกับภาษี/, english: /tax invoice/i, word: "tax invoice" },
    { thai: /ใบรับของ/, english: /goods receipt/i, word: "goods receipt" },
    { thai: /ข้อยกเว้น/, english: /\bexceptions?\b/i, word: "exception" },
  ];

  for (const term of TERMS) {
    it(`${term.thai.source} is “${term.word}”`, () => {
      const off = Object.keys(th)
        .filter((key) => term.thai.test(th[key]!) && !term.english.test(en[key] ?? "") && !(term.except && key in term.except))
        .map((key) => `${key}: ${en[key]}`);
      expect(off).toEqual([]);
    });
  }

  it("a run is never a “round”, and the words the glossary rules out are not used", () => {
    const ruledOut = /\brounds?\b|\bprovenance\b|\baudit trail\b|\bverif(y|ied|ication)\b|\bvalidat(e|ed|ion)\b|\bVAT invoice\b|\bworksheet\b|\binstal+ment\b|\bon hold\b|\bplease\b|\boops\b|!/i;
    const found = Object.entries(en)
      .filter(([, value]) => ruledOut.test(value))
      .map(([key, value]) => `${key}: ${value}`);
    expect(found).toEqual([]);
  });

  it("money in a sentence is “THB {amount}”, never an amount followed by baht", () => {
    for (const [key, value] of Object.entries(th)) {
      if (!/\{\w+\} บาท/.test(value)) continue;
      expect(en[key], key).toMatch(/THB \{\w+\}/);
    }
    // (a placeholder may be NAMED {baht}; the word is what must not be written)
    expect(Object.entries(en).filter(([, v]) => /\bbaht\b/i.test(v.replace(/\{\w+\}/g, ""))).map(([k]) => k)).toEqual([]);
  });
});

describe("Agent Market dictionaries — a sentence for a person", () => {
  it("no English sentence carries the words the server takes for an error's (GLOSSARY.md §6 rule 4)", () => {
    // “exception” alone is the glossary's own word for a flagged row (ข้อยกเว้น); what reads
    // like an error is an exception's name or “exception:”, as workspace/personText.ts has it.
    const errorText = [
      /\bnot found\b/i,
      /\btimed out\b/i,
      /\bpermission denied\b/i,
      /\bundefined\b/i,
      /\bconstraint\b/i,
      /\bviolates\b/i,
      /\b(?:error|fatal|warning|exception):/i,
      /\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b/,
      /\bline \d+\b/i,
      /\bhttps?:\/\/\S+/i,
      /\b\d{1,3}(?:\.\d{1,3}){3}\b/,
    ];
    const found = Object.entries(en)
      .filter(([, value]) => errorText.some((re) => re.test(value)))
      .map(([key, value]) => `${key}: ${value}`);
    expect(found).toEqual([]);
  });

  it("an example date is shown in the language's own form (GLOSSARY.md §4)", () => {
    for (const key of ["typed.hint.date", "typed.err.date"]) {
      expect(th[key], key).toContain("15/09/2569");
      expect(en[key], key).toContain("15/09/2026");
      expect(en[key], key).not.toMatch(/25\d\d/);
    }
  });
});

describe("Agent Market lookup — follows the app's language", () => {
  const key: AmKey = "round.start";

  it("reads the app's language at the moment of the call", () => {
    useI18nStore.setState({ locale: "th" });
    expect(tAm(key)).toBe("เริ่มรอบใหม่");
    useI18nStore.setState({ locale: "en" });
    expect(tAm(key)).toBe("Start a new run");
  });

  it("fills the placeholders in either language, and leaves one it was not given as written", () => {
    useI18nStore.setState({ locale: "en" });
    expect(tAm("install.stepOf", { n: "2", total: "5" })).toBe("Step 2 of 5");
    expect(tAm("install.stepOf", { n: "2" })).toBe("Step 2 of {total}");
    useI18nStore.setState({ locale: "th" });
    expect(tAm("install.stepOf", { n: "2", total: "5" })).toBe("ขั้นที่ 2 จาก 5");
  });

  it("a key English has no word for reads in Thai, and a key nobody has reads as the key", () => {
    const english = amDictionaries.en as Record<string, string>;
    const kept = english[key]!;
    useI18nStore.setState({ locale: "en" });
    try {
      delete english[key];
      expect(tAm(key)).toBe("เริ่มรอบใหม่");
    } finally {
      english[key] = kept;
    }
    expect(tAm(key)).toBe("Start a new run");
    expect(tAm("no.such.key" as AmKey)).toBe("no.such.key");
  });
});
