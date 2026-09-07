// FIX84 row 2 — the slash menu offers exactly what the toolbar offers.
//
// Before: /image and /video were listed for every model; on a model that
// cannot generate they cleared the composer and did nothing (FIX83 #2).

import { describe, expect, it } from "vitest";

import { applySlashAvailability, SLASH_COMMANDS, type SlashAvailability } from "./slashCommands";

const ALL_ON: SlashAvailability = {
  canGenerateImages: true,
  imageFeatureAvailable: true,
  canGenerateVideos: true,
  videoFeatureAvailable: true,
  hosted: false,
};

const ids = (a: SlashAvailability) => applySlashAvailability(SLASH_COMMANDS, a).map((c) => `${c.id}${c.disabledReasonKey ? "!" : ""}`);

describe("applySlashAvailability (FIX84 row 2)", () => {
  it("everything usable → every command listed, none disabled", () => {
    expect(ids(ALL_ON)).toEqual(["image", "video", "new", "shortcuts"]);
  });

  it("the selected model cannot make images but another model can → /image listed DISABLED with the toolbar's reason", () => {
    const out = applySlashAvailability(SLASH_COMMANDS, { ...ALL_ON, canGenerateImages: false });
    const image = out.find((c) => c.id === "image")!;
    expect(image.disabledReasonKey).toBe("input.imageMode.needsTools");
  });

  it("nothing on this deployment can make images → /image hidden (no affordance for what nothing can do)", () => {
    expect(ids({ ...ALL_ON, canGenerateImages: false, imageFeatureAvailable: false })).toEqual(["video", "new", "shortcuts"]);
  });

  it("video: disabled-with-reason when the feature exists but the model cannot drive it; hidden when the feature is absent", () => {
    const disabled = applySlashAvailability(SLASH_COMMANDS, { ...ALL_ON, canGenerateVideos: false });
    expect(disabled.find((c) => c.id === "video")!.disabledReasonKey).toBe("input.videoMode.needsTools");
    expect(ids({ ...ALL_ON, canGenerateVideos: false, videoFeatureAvailable: false })).toEqual(["image", "new", "shortcuts"]);
  });

  it("video is hidden on the desktop host, exactly like its toolbar button", () => {
    expect(ids({ ...ALL_ON, hosted: true })).toEqual(["image", "new", "shortcuts"]);
  });

  it("a model that gains the capability later is simply usable again (no stale state to update)", () => {
    const before = applySlashAvailability(SLASH_COMMANDS, { ...ALL_ON, canGenerateImages: false });
    const after = applySlashAvailability(SLASH_COMMANDS, ALL_ON);
    expect(before.find((c) => c.id === "image")!.disabledReasonKey).toBeDefined();
    expect(after.find((c) => c.id === "image")!.disabledReasonKey).toBeUndefined();
  });

  it("non-media commands and skill templates pass through untouched", () => {
    const skill = { id: "skill" as const, keyword: "สรุป", labelKey: "slash.image" as const, hintKey: "slash.image.hint" as const, label: "สรุป", skillId: "s1" };
    const out = applySlashAvailability([...SLASH_COMMANDS, skill], { ...ALL_ON, canGenerateImages: false, imageFeatureAvailable: false, hosted: true });
    expect(out.map((c) => c.id)).toEqual(["new", "shortcuts", "skill"]);
    expect(out.every((c) => !c.disabledReasonKey)).toBe(true);
  });
});
