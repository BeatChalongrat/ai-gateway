import type { VisualBox } from "./visualEditor";

/** Snap page/peer edges and centres in document coordinates (not CSS pixels). */
export function snapBox(box: VisualBox, peers: VisualBox[], width: number, height: number, resize = false) {
  const out = { ...box }; const guides: { axis: "x" | "y"; at: number }[] = [];
  for (const axis of ["x", "y"] as const) {
    const pos = axis === "x" ? "left" : "top"; const size = axis === "x" ? "width" : "height"; const extent = axis === "x" ? width : height;
    const targets = [0, extent / 2, extent, ...peers.flatMap(p => [p[pos], p[pos] + p[size] / 2, p[pos] + p[size]])];
    const anchors = resize ? [out[pos] + out[size]] : [out[pos], out[pos] + out[size] / 2, out[pos] + out[size]];
    let best = 5; let delta = 0; let at: number | undefined;
    for (const target of targets) for (const anchor of anchors) if (Math.abs(target - anchor) < best) { best = Math.abs(target - anchor); delta = target - anchor; at = target; }
    if (resize) out[size] = Math.max(12, Math.min(extent - out[pos], out[size] + delta));
    else out[pos] = Math.max(0, Math.min(extent - out[size], out[pos] + delta));
    if (at !== undefined) guides.push({ axis, at });
  }
  return { box: out, guides };
}
