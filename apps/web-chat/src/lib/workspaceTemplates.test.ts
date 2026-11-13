import { expect, it } from "vitest";
import { templateTable } from "./workspaceTemplates";
import { snapBox } from "./editorGeometry";
it("types budget amounts and keeps CSV cells literal", () => {
  expect(templateTable("xlsx.budget", "Design\t120\t100").rows).toEqual([{ item: "Design", planned: 120, actual: 100 }]);
  expect(templateTable("csv.data_export", "id\tformula\n00123\t=1+1").rows).toEqual([{ id: "00123", formula: "=1+1" }]);
  expect(() => templateTable("xlsx.budget", "Design\tunknown\t100")).toThrow();
  expect(() => templateTable("csv.data_export", "a\ta\nx\ty")).toThrow();
});
it("snaps frame edges and constrains resizing to the page", () => {
  const snap = snapBox({ left: 48, top: 50, width: 100, height: 30 }, [{ left: 50, top: 90, width: 100, height: 30 }], 600, 800);
  expect(snap.box.left).toBe(50); expect(snap.guides).toContainEqual({ axis: "x", at: 50 });
  const resized = snapBox({ left: 500, top: 750, width: 200, height: 200 }, [], 600, 800, true);
  expect(resized.box).toEqual({ left: 500, top: 750, width: 100, height: 50 });
});
