// E4 — folder grants: the same rules as FolderGrantValidator (absolute,
// exists, not a drive root, not a system/app-state folder), applied when a
// grant is added AND when the file is loaded, so a hand-edited grants.json
// cannot smuggle a forbidden root; and the snapshot that travels with every
// sidecar call.
//
// LIMITS: the sidecar re-validates each entry independently (C#,
// FolderGrantValidator) and refuses a call with no list at all — that half
// is proven live by `--probe-e4 refusal`, not here.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { GrantStore, defaultForbiddenRoots, validateGrant } from "../src/main/grants";

const rules = { forbiddenRoots: ["C:\\Windows", "C:\\Program Files", "C:\\Users\\tester\\AppData\\Roaming\\SRAN Desktop", "C:\\Users\\tester\\AppData\\Roaming\\SranDesktop-Electron"] };
const exists = (p: string) => !/missing/i.test(p);

describe("validateGrant", () => {
  it("accepts an existing absolute folder and canonicalises it", () => {
    expect(validateGrant("C:\\Users\\tester\\Desktop\\proj\\", rules, exists)).toEqual({ verdict: "Ok", canonical: "C:\\Users\\tester\\Desktop\\proj" });
  });
  it("refuses relative, drive roots, system folders, the apps' own state, and missing folders", () => {
    expect(validateGrant("Desktop\\proj", rules, exists).verdict).toBe("NotAbsolute");
    expect(validateGrant("C:\\", rules, exists).verdict).toBe("DriveRoot");
    expect(validateGrant("C:", rules, exists).verdict).toBe("DriveRoot");
    expect(validateGrant("C:\\Windows\\System32", rules, exists).verdict).toBe("SystemFolder");
    expect(validateGrant("C:\\Program Files\\App", rules, exists).verdict).toBe("SystemFolder");
    expect(validateGrant("C:\\Users\\tester\\AppData\\Roaming\\SranDesktop-Electron\\history", rules, exists).verdict).toBe("SystemFolder");
    expect(validateGrant("C:\\Users\\tester\\missing", rules, exists).verdict).toBe("NotADirectory");
    expect(validateGrant("C:\\bad|name", rules, exists).verdict).toBe("Invalid");
  });
});

describe("GrantStore", () => {
  it("round-trips, de-duplicates, revokes, and re-validates on load", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "e4-grants-"));
    const file = path.join(dir, "grants.json");
    const real = fs.mkdtempSync(path.join(os.tmpdir(), "e4-granted-"));
    const store = new GrantStore(file, { forbiddenRoots: ["C:\\Windows"] });
    expect(store.add(real).verdict).toBe("Ok");
    expect(store.add(real + "\\").verdict).toBe("Ok");
    expect(store.list().length).toBe(1);
    expect(store.add("C:\\Windows\\Temp").verdict).toBe("SystemFolder");
    // Smuggle a forbidden root into the file by hand → dropped on load.
    fs.writeFileSync(file, JSON.stringify({ grants: [real, "C:\\Windows\\Temp", "relative\\x"] }));
    const reloaded = new GrantStore(file, { forbiddenRoots: ["C:\\Windows"] });
    expect(reloaded.list()).toEqual([path.win32.normalize(real)]);
    expect(reloaded.remove(real)).toBe(true);
    expect(reloaded.list()).toEqual([]);
  });
  it("forbidden roots include both apps' state dirs and the OS folders", () => {
    const roots = defaultForbiddenRoots({ APPDATA: "C:\\Users\\t\\AppData\\Roaming", LOCALAPPDATA: "C:\\Users\\t\\AppData\\Local", SystemRoot: "C:\\Windows", ProgramFiles: "C:\\Program Files", "ProgramFiles(x86)": "C:\\Program Files (x86)" }, "C:\\Users\\t\\AppData\\Roaming\\SranDesktop-Electron");
    expect(roots).toContain("C:\\Users\\t\\AppData\\Roaming\\SRAN Desktop");
    expect(roots).toContain("C:\\Users\\t\\AppData\\Roaming\\SranDesktop-Electron");
    expect(roots).toContain("C:\\Windows");
    expect(roots).toContain("C:\\Windows\\System32");
  });
});
