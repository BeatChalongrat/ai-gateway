// FIX134 F — area 6: [TEST] alert routing. Fires one test alert the way FIX131/FIX133 did
// (python -m app.ops.fire_test_alert, in the ops-watch container on live, the backend on the
// test stack): it is always "[TEST]" and goes ONLY to ALERT_TEST_NTFY_URL. The check asserts the
// delivery record names the test channel alone and that the channel took it.
"use strict";
const L = require("./lib");

module.exports = async function alert(R) {
  await R.run("alert.test_routing", async () => {
    const container = L.T.containers.opsWatch || L.T.containers.backend;
    if (!L.containerRunning(container)) return { ok: false, detail: `${container} is not running` };
    const msg = `[SMOKE] ${L.TARGET} smoke suite ${new Date().toISOString()} — ignore`;
    const r = L.dockerExec(container, ["python", "-m", "app.ops.fire_test_alert", msg], { timeoutMs: 60000 });
    const jsonLine = r.out.split(/\r?\n/).reverse().find((l) => /"channels"/.test(l));
    let rec = null;
    try { rec = JSON.parse(jsonLine); } catch { /* handled below */ }
    if (!rec) return { ok: false, detail: `${container}: no channels record in the output: ${r.out.trim().slice(-160)}` };
    const channels = rec.channels || [];
    const delivered = Object.entries(rec.outcome || {});
    const onlyTest = channels.length === 1 && channels[0] === "ntfy-test" && delivered.every(([c]) => c === "ntfy-test");
    // structlog's delivery line, when present, must not name another channel either
    const deliveredLine = r.out.split(/\r?\n/).find((l) => /alert_delivered/.test(l)) || "";
    const strayChannel = /"(ntfy|email|webhook)"\s*:/.test(deliveredLine);
    if (channels.length === 0) return { ok: false, detail: "no test channel configured (ALERT_TEST_NTFY_URL) — nothing was sent" };
    return { ok: onlyTest && !strayChannel && delivered.every(([, ok]) => ok === true), detail: `channels=${JSON.stringify(channels)} outcome=${JSON.stringify(rec.outcome)}${strayChannel ? " — the delivery line names another channel" : ""}` };
  });
};
