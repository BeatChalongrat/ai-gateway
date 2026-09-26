// node --test scripts/smoke/desktop.test.js
// AM1b §3: desktop.turn watched every button whose label *contained* หยุด/Stop,
// so a chat titled "[TEST e50] C1 หยุดก่อนตอบ" in the sidebar kept it red for
// ever. Only the composer's own button, with the exact Stop label, counts.
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { isStopButton } = require("./desktop.js");

test("the composer's stop button counts", () => {
  assert.equal(isStopButton({ label: "หยุด", inComposer: true }), true);
  assert.equal(isStopButton({ label: "Stop generation", inComposer: true }), true);
});

test("a sidebar chat whose title contains หยุด does not", () => {
  assert.equal(isStopButton({ label: "Conversation actions: “[TEST e50] C1 หยุดก่อนตอบ”", inComposer: false }), false);
});

test("other stop buttons are not the generation stop", () => {
  assert.equal(isStopButton({ label: "หยุดอัดเสียง", inComposer: true }), false);
  assert.equal(isStopButton({ label: "Send", inComposer: true }), false);
});
