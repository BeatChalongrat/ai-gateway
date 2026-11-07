#!/usr/bin/env python3
"""Curate the chat picker to at most 5 models per provider (FIX47.6 Part A).

Data-and-tooling task on top of the FIX47 two-axis catalogue:
``is_active`` (the API may call it) is never touched; only
``show_in_chat`` (the chat picker offers it) moves, and only through the
EXISTING ``PATCH /admin/models/{id}`` endpoint — no new API surface, no
direct SQL, no hardcoded allow-list in application code (the model row's
own comment forbids one).

Three modes; the selection is provisional by design (the owner will trim
again), so every step is cheap to re-run and cheap to undo:

  --propose [--probe-results F]   rank + write the proposed table
                                  (markdown + CSV). Writes NOTHING.
  --apply --from proposal.csv     snapshot current show_in_chat of every
                                  row it will touch, then PATCH.
                                  Idempotent: a second run changes nothing.
  --restore snapshot.json         put every row back exactly as recorded.

Ranking (in order):
  1. alive       — the model answered the FIX47.6 Part B probe on THIS
                   provider route. Dead models are never proposed.
  2. capability  — probe-OBSERVED tools, then vision, then reasoning
                   (reasoning is the stored flag: the probe does not
                   verify it — flagged as such in the reason column).
  3. recency     — the repo has no reliable recency signal, so this axis
                   is last_synced_at + model id (documented as WEAK; no
                   invented version parser).
  4. cost band   — at least one model from the provider's cheapest
                   cost_tier survives, so curation never removes the
                   low-cost option entirely.

Auth: the backend admin surface is cookie-session only. Set ADMIN_PASS
(and optionally ADMIN_USER, default "admin"); the script logs in via
POST /auth/session/login and sends the CSRF header on writes.

    ADMIN_PASS=... python scripts/curate_chat_models.py --propose \
        --probe-results scripts/probes/capability-flags/results/<ts>.json
    ADMIN_PASS=... python scripts/curate_chat_models.py --apply --from data/curation-proposals/<ts>.csv
    ADMIN_PASS=... python scripts/curate_chat_models.py --restore data/curation-snapshots/<ts>.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
PROPOSAL_DIR = REPO / "data" / "curation-proposals"
SNAPSHOT_DIR = REPO / "data" / "curation-snapshots"
CEILING = 5

CAP_ORDER = {"budget": 0, "standard": 1, "premium": 2}


class AdminClient:
    """Cookie-session client for the /admin surface (stdlib only)."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.cookies: dict[str, str] = {}

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.base + path, method=method)
        req.add_header("Content-Type", "application/json")
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        if "csrf_token" in self.cookies and method in ("POST", "PATCH", "DELETE"):
            req.add_header("X-CSRF-Token", self.cookies["csrf_token"])
        data = json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(req, data=data) as resp:
            for header, value in resp.headers.items():
                if header.lower() == "set-cookie":
                    name, _, rest = value.partition("=")
                    self.cookies[name.strip()] = rest.split(";", 1)[0]
            return json.loads(resp.read() or b"{}")

    def login(self, email: str, password: str) -> None:
        self._request("POST", "/auth/session/login", {"email": email, "password": password})

    def list_configs(self) -> list[dict]:
        return self._request("GET", "/admin/models/configs?limit=500")["items"]

    def patch_model(self, model_id: int, show_in_chat: bool) -> dict:
        return self._request("PATCH", f"/admin/models/{model_id}", {"show_in_chat": show_in_chat})


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------

def load_probe(path: pathlib.Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    by_pair = {(r["provider"], r["model"]): r for r in report.get("results", [])}
    return {"providers": set(report.get("providers_with_valid_keys", [])), "pairs": by_pair}


def rank_provider(rows: list[dict], probe_pairs: dict) -> list[dict]:
    """Return the provider's chat rows, decided + annotated.

    Each returned dict gains: ``decision`` ("show"/"hide"), ``reason``.
    """
    decided: list[dict] = []
    candidates: list[dict] = []
    for r in rows:
        p = probe_pairs.get((r["provider"], r["model"]))
        # alive: True (answered), False (definitively dead), None
        # (UNKNOWN — the account was out of credits or throttled at
        # probe time; a billing state must not disqualify a provider's
        # whole catalogue, so these rank on the STORED flags, flagged).
        alive = p.get("alive") if p else False
        r = dict(r)
        r["_alive"] = alive
        r["_tools"] = bool(p["observed_supports_tools"]) if p and "observed_supports_tools" in p else bool(r["supports_tools"])
        r["_vision"] = bool(p["observed_supports_vision"]) if p and "observed_supports_vision" in p else bool(r["supports_vision"])
        r["_verified"] = bool(p and alive is True)
        if not p:
            r["decision"], r["reason"] = "hide", "not probed (no probe result for this pair)"
            decided.append(r)
        elif alive is False:
            err = (p.get("alive_error") or "")[:80]
            r["decision"], r["reason"] = "hide", f"dead: probe got no answer ({err})"
            decided.append(r)
        else:
            if alive is None:
                r["_unverified_note"] = (p.get("outcome") or "").replace("skipped_", "")
            candidates.append(r)

    # Capability score from OBSERVED behaviour; reasoning stays the
    # stored flag (unverified — the probe has no reasoning leg).
    def score(r: dict) -> tuple:
        return (
            int(r["_tools"]) * 4 + int(r["_vision"]) * 2 + int(r["supports_reasoning"]),
            r.get("last_synced_at") or "",
            r["model"],  # WEAK recency proxy — flagged in the report
        )

    candidates.sort(key=score, reverse=True)
    pick_ids = {r["id"] for r in candidates[:CEILING]}
    swapped_out: int | None = None

    # Cost-band keeper: the cheapest tier present among ALIVE candidates
    # must survive, so a low-cost option always remains (mirrors keeping
    # Haiku permanently available in Anthropic's own org controls).
    if candidates:
        cheapest = min(CAP_ORDER.get(r["cost_tier"], 1) for r in candidates)
        picks = [r for r in candidates if r["id"] in pick_ids]
        if picks and not any(CAP_ORDER.get(r["cost_tier"], 1) == cheapest for r in picks):
            keeper = next(r for r in candidates if CAP_ORDER.get(r["cost_tier"], 1) == cheapest)
            swapped_out = picks[-1]["id"]
            pick_ids.discard(swapped_out)
            pick_ids.add(keeper["id"])

    for r in candidates:
        if r["id"] in pick_ids:
            caps = [c for c, on in (("tools", r["_tools"]), ("vision", r["_vision"]),
                                    ("reasoning*", r["supports_reasoning"])) if on]
            state = "alive" if r["_verified"] else f"ALIVE UNVERIFIED ({r.get('_unverified_note', '?')}; stored flags used)"
            r["decision"] = "show"
            r["reason"] = f"{state}; {'+'.join(caps) if caps else 'no extra capabilities'}; {r['cost_tier']}"
            if CAP_ORDER.get(r["cost_tier"], 1) == min(CAP_ORDER.get(x["cost_tier"], 1) for x in candidates):
                r["reason"] += "; cheapest band"
        elif r["id"] == swapped_out:
            r["decision"] = "hide"
            r["reason"] = "outranked by the cheap-band keeper"
        else:
            r["decision"] = "hide"
            r["reason"] = "outranked (ceiling is 5)"
        decided.append(r)
    return decided


def propose(client: AdminClient, probe_path: pathlib.Path) -> int:
    probe = load_probe(probe_path)
    items = client.list_configs()
    chat_rows = [
        r for r in items
        if r["is_active"] and r["provider"] in probe["providers"]
    ]
    # Media rows never ride the picker's show_in_chat filter the same
    # way (they have their own kind sections) — leave them alone.
    media_pairs = {pair for pair, r in probe["pairs"].items() if r.get("media")}
    chat_rows = [r for r in chat_rows if (r["provider"], r["model"]) not in media_pairs]

    by_provider: dict[str, list[dict]] = {}
    for r in chat_rows:
        by_provider.setdefault(r["provider"], []).append(r)

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = PROPOSAL_DIR / f"{stamp}.csv"
    md_path = PROPOSAL_DIR / f"{stamp}.md"

    all_decided: list[dict] = []
    md = io.StringIO()
    md.write(f"# Chat-picker curation proposal — {stamp}\n\n")
    md.write("Ceiling: at most 5 shown per provider. `is_active` untouched — API\n")
    md.write("callers keep the full catalogue. `reasoning*` = stored flag, not\n")
    md.write("probe-verified. Recency axis is weak (last_synced_at + model id).\n\n")
    for provider in sorted(by_provider):
        decided = rank_provider(by_provider[provider], probe["pairs"])
        all_decided.extend(decided)
        shown = [d for d in decided if d["decision"] == "show"]
        md.write(f"## {provider} — propose {len(shown)} shown / {len(decided)} active chat models\n\n")
        md.write("| decision | model | reason |\n|---|---|---|\n")
        for d in sorted(decided, key=lambda x: (x["decision"] != "show", x["model"])):
            md.write(f"| {d['decision']} | `{d['model']}` | {d['reason']} |\n")
        md.write("\n")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "provider", "model", "decision", "reason"])
        for d in all_decided:
            w.writerow([d["id"], d["provider"], d["model"], d["decision"], d["reason"]])
    md_path.write_text(md.getvalue(), encoding="utf-8")

    print(md.getvalue())
    print(f"proposal (CSV, for --apply --from): {csv_path.relative_to(REPO)}")
    print(f"proposal (markdown):                {md_path.relative_to(REPO)}")
    print("\nNothing was written to the database.")
    return 0


# ---------------------------------------------------------------------------
# Apply / restore
# ---------------------------------------------------------------------------

def apply(client: AdminClient, proposal_path: pathlib.Path) -> int:
    with proposal_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    wanted = {int(r["id"]): (r["decision"] == "show") for r in rows}

    current = {r["id"]: r for r in client.list_configs()}
    # Refuse a proposal that would break the ceiling (edited by hand?).
    per_provider: dict[str, int] = {}
    for mid, show in wanted.items():
        if show and mid in current:
            p = current[mid]["provider"]
            per_provider[p] = per_provider.get(p, 0) + 1
    over = {p: n for p, n in per_provider.items() if n > CEILING}
    if over:
        sys.exit(f"refusing to apply: proposal exceeds the {CEILING}-per-provider ceiling: {over}")

    to_change = {
        mid: show for mid, show in wanted.items()
        if mid in current and bool(current[mid]["show_in_chat"]) != show
    }
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    snap_path = SNAPSHOT_DIR / f"{stamp}.json"
    snapshot = [
        {"id": mid, "provider": current[mid]["provider"], "model": current[mid]["model"],
         "show_in_chat": bool(current[mid]["show_in_chat"])}
        for mid in sorted(wanted)
        if mid in current
    ]
    snap_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"snapshot of current state: {snap_path.relative_to(REPO)}")

    if not to_change:
        print("nothing to change — already in the proposed state (idempotent).")
        return 0
    for mid, show in sorted(to_change.items()):
        r = client.patch_model(mid, show)
        print(f"  {r['provider']:12} {r['model']:44} show_in_chat -> {show}")
    print(f"changed {len(to_change)} rows. Undo with: --restore {snap_path.relative_to(REPO)}")
    return 0


def restore(client: AdminClient, snapshot_path: pathlib.Path) -> int:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    current = {int(r["id"]): r for r in client.list_configs()}
    changed = 0
    for row in snapshot:
        mid = int(row["id"])
        want = bool(row["show_in_chat"])
        if mid in current and bool(current[mid]["show_in_chat"]) != want:
            client.patch_model(mid, want)
            print(f"  {row['provider']:12} {row['model']:44} show_in_chat -> {want}")
            changed += 1
    print(f"restored {changed} rows to the snapshot state ({len(snapshot) - changed} already matched).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--propose", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", metavar="SNAPSHOT")
    ap.add_argument("--from", dest="from_file", metavar="PROPOSAL_CSV")
    ap.add_argument("--probe-results", metavar="PROBE_JSON")
    ap.add_argument("--base-url", default="http://127.0.0.1:28080")
    args = ap.parse_args()

    password = os.environ.get("ADMIN_PASS")
    if not password:
        sys.exit("error: set ADMIN_PASS (admin login; the admin surface is session-cookie only)")
    client = AdminClient(args.base_url)
    client.login(os.environ.get("ADMIN_USER", "admin"), password)

    if args.propose:
        if not args.probe_results:
            sys.exit("error: --propose needs --probe-results <capability probe JSON> "
                     "(run scripts/probe_model_capabilities.py first — dead or unprobed "
                     "models are never proposed)")
        return propose(client, pathlib.Path(args.probe_results))
    if args.apply:
        if not args.from_file:
            sys.exit("error: --apply needs --from <proposal.csv>")
        return apply(client, pathlib.Path(args.from_file))
    return restore(client, pathlib.Path(args.restore))


if __name__ == "__main__":
    raise SystemExit(main())
