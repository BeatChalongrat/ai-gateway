"""FIX38 Part D — does the ENGLISH NER layer (ner-service + Presidio) earn its keep?

The service costs a 1.89 GB image and ~1.5-2.9 GiB of RAM. Two rounds
have shown it contributes exactly nothing on Thai text; nobody had
measured English. This probe does, on a synthetic English set, by
scoring the SAME Python registry with the NER axis on and off:

    on   = get_default_registry(ner_enabled=True)   -> presidio_ner calls ner-service
    off  = get_default_registry(ner_enabled=False)  -> regex fallback only

What it reports, per arm:
    * recall on planted PERSON / LOCATION / ORGANIZATION values
    * recall on planted email / phone (regex territory — a control)
    * fires on a clean English prose corpus (must-not-fire)
    * per-sentence latency p50 / p95 / p99

Everything below is invented: no real person, company, address or
number. Run inside the backend container:

    python -m evals.dlp.english_ner_probe --out /tmp/english_ner_probe.json

Note on production relevance: ``org_settings.ner_enabled`` is FALSE on
this deployment, so the English layer is switched off for every tenant
today regardless of what this probe finds. The probe answers "would it
help if it were on?", which is the question behind keeping the service.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ── synthetic must-catch set ──────────────────────────────────────────────

PERSON = [
    ("Marcus Whitfield", "Can you draft a reply to {v} about the delayed shipment?"),
    ("Priya Ramaswamy", "{v} called twice this morning asking for the invoice."),
    ("Tomasz Nowicki", "Please loop in {v} before we send the contract."),
    ("Aisha Bello", "The complaint was filed by {v} on Tuesday."),
    ("Kenji Okamura", "I met {v} at the Bangkok office last week."),
    ("Helena Strand", "{v} is the new account manager for the northern region."),
    ("Diego Alvarez", "Forward the ticket to {v}; he owns that integration."),
    ("Ingrid Halvorsen", "Summarise the notes from my call with {v}."),
    ("Samuel Adeyemi", "{v} says the payment bounced again."),
    ("Chloe Bernard", "Book a follow-up with {v} for Thursday afternoon."),
    ("Rafael Duarte", "Who approved the refund for {v}?"),
    ("Mei-Ling Tan", "{v} reported the login issue from the Singapore branch."),
    ("Olusegun Bakare", "Translate this message for {v} into Thai."),
    ("Anneliese Roth", "The reference letter for {v} needs a signature."),
    ("Viktor Petrov", "{v} asked whether the warranty covers water damage."),
    ("Farah Haddad", "Draft a polite reminder to {v} about the overdue balance."),
    ("Liam Gallagher-Smyth", "{v} escalated the case to the regional director."),
    ("Nadia Kowalczyk", "Please send {v} the updated price list."),
    ("Hiroshi Tanaka", "Can {v} join the demo on Friday?"),
    ("Beatriz Oliveira", "{v} wants to cancel the subscription effective next month."),
    ("Mr. Whitfield", "Tell {v} the parts arrive on Monday."),
    ("Dr. Ramaswamy", "{v} needs the lab results before noon."),
    ("Ms. Bello", "Reply to {v} confirming the appointment."),
    ("Kenji", "Ask {v} to resend the attachment; the file was corrupted."),
    ("Priya", "{v} is out until the 14th, route her tickets to the queue."),
]
LOCATION = [
    ("Chiang Mai", "Ship the replacement unit to our {v} warehouse."),
    ("Denver", "The customer is based in {v} and wants a local technician."),
    ("Kuala Lumpur", "Our {v} office will handle the onboarding."),
    ("Rotterdam", "The container is stuck at the port of {v}."),
    ("Osaka", "Schedule the audit for the {v} plant."),
    ("Sukhumvit Road", "The meeting point is the cafe on {v}."),
    ("Nairobi", "We opened a service desk in {v} last quarter."),
    ("Lyon", "Translate the {v} branch report into English."),
]
ORGANIZATION = [
    ("Nimbus Logistics", "{v} disputes the late-delivery penalty."),
    ("Harbourline Insurance", "Send the claim form to {v} today."),
    ("Kestrel Robotics", "{v} wants a demo of the new firmware."),
    ("Bluefin Dental Clinic", "The invoice from {v} is still unpaid."),
    ("Orchard & Vale Ltd", "Prepare the renewal quote for {v}."),
    ("Sunward Microfinance", "{v} asked for a data-processing agreement."),
]
CONTROL = [
    ("marcus.whitfield@nimbus-logistics.example", "email", "Contact is {v} for anything urgent."),
    ("priya.r@harbourline.example", "email", "Send the PDF to {v} please."),
    ("+1 303-555-0142", "phone", "Call {v} if the driver is late."),
    ("081-234-5678", "phone", "Her mobile is {v}, Thai number."),
    ("(02) 555-0199", "phone", "Office line {v}, ask for extension 12."),
]

# ── must-not-fire English prose ───────────────────────────────────────────
CLEAN = [
    "Please summarise the attached quarterly report in three bullet points.",
    "What is the difference between a lease and a rental agreement?",
    "Rewrite this paragraph in a more formal tone.",
    "The server restarted twice overnight; check the memory graphs.",
    "Draft an agenda for tomorrow's planning meeting.",
    "Explain how compound interest works with a simple example.",
    "Our return policy allows exchanges within fourteen days of purchase.",
    "Translate 'thank you for your patience' into Thai and Japanese.",
    "List five common causes of high customer churn.",
    "The warehouse closes early on public holidays.",
    "Generate a short product description for a stainless steel water bottle.",
    "Which spreadsheet formula sums a column only when another column says Paid?",
    "Suggest a subject line for a newsletter about the spring sale.",
    "What are the pros and cons of monthly versus annual billing?",
    "Turn these meeting notes into action items with owners.",
    "How long should a password be, and should it be rotated?",
    "The printer on the third floor is out of toner again.",
    "Write a two-sentence apology for a delayed delivery.",
    "Compare the two proposals and highlight the cost differences.",
    "Remind me what the acronym SLA stands for.",
    "The monsoon season usually starts in May and ends around October.",
    "Give me a checklist for onboarding a new supplier.",
    "Is it better to store dates as text or as date types in a database?",
    "Outline a training plan for the new support hires.",
    "What does 'net 30' mean on an invoice?",
    "Summarise the main risks of migrating email to a new provider.",
    "Draft a polite decline for a vendor meeting request.",
    "The elevator inspection is scheduled for the last Friday of the month.",
    "Explain the difference between gross margin and net margin.",
    "Recommend three books on negotiation for a sales team.",
    "The coffee machine in the lobby needs descaling.",
    "Write a short welcome message for the customer portal.",
    "How do I export a chart from the dashboard as an image?",
    "What is a reasonable response time target for email support?",
    "Create a table comparing the three subscription tiers.",
    "The parking garage will be repainted next weekend.",
    "Suggest ways to reduce paper use in the office.",
    "Explain what a webhook is to a non-technical colleague.",
    "Draft a reminder about submitting expense reports by the fifth.",
    "What is the capital gains treatment of a short-term holding, in general terms?",
]


def _items():
    out = []
    for i, (v, frame) in enumerate(PERSON):
        out.append({"id": f"per{i+1:02d}", "kind": "person", "value": v, "text": frame.format(v=v)})
    for i, (v, frame) in enumerate(LOCATION):
        out.append({"id": f"loc{i+1:02d}", "kind": "location", "value": v, "text": frame.format(v=v)})
    for i, (v, frame) in enumerate(ORGANIZATION):
        out.append({"id": f"org{i+1:02d}", "kind": "organization", "value": v, "text": frame.format(v=v)})
    for i, (v, kind, frame) in enumerate(CONTROL):
        out.append({"id": f"ctl{i+1:02d}", "kind": kind, "value": v, "text": frame.format(v=v)})
    return out


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return round(xs[k] * 1000, 2)


def run_arm(ner_enabled: bool) -> dict:
    from app.services.dlp.detectors.registry import get_default_registry

    reg = get_default_registry(ner_enabled=ner_enabled, thai_ner=False)
    by_kind: dict[str, list[int]] = {}
    caught_by: dict[str, dict[str, int]] = {}
    misses: list[dict] = []
    lat: list[float] = []
    for it in _items():
        t0 = time.perf_counter()
        ms = reg.scan_text(it["text"], language="en")
        lat.append(time.perf_counter() - t0)
        v = it["value"].lower()
        hit = [m for m in ms if v in m.matched_text.lower() or m.matched_text.lower() in v and len(m.matched_text) >= 4]
        ok = int(bool(hit))
        by_kind.setdefault(it["kind"], []).append(ok)
        for m in hit:
            caught_by.setdefault(it["kind"], {}).setdefault(m.detector_name, 0)
            caught_by[it["kind"]][m.detector_name] += 1
        if not ok:
            misses.append({"id": it["id"], "kind": it["kind"]})
    fires = []
    for i, s in enumerate(CLEAN):
        t0 = time.perf_counter()
        ms = reg.scan_text(s, language="en")
        lat.append(time.perf_counter() - t0)
        if ms:
            fires.append({"id": f"clean{i+1:02d}", "detectors": sorted({m.detector_name for m in ms}),
                          "matched": [m.matched_text for m in ms][:4]})
    return {
        "ner_enabled": ner_enabled,
        "has_presidio_ner": "presidio_ner" in reg,
        "recall": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_kind.items())},
        "counts": {k: len(v) for k, v in sorted(by_kind.items())},
        "caught_by_detector": caught_by,
        "misses": misses,
        "clean_corpus": {"sentences": len(CLEAN), "fires": len(fires), "detail": fires},
        "latency_ms": {"p50": _pct(lat, 50), "p95": _pct(lat, 95), "p99": _pct(lat, 99), "n": len(lat)},
    }


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    res = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "arms": {}}
    for arm in (False, True):
        r = run_arm(arm)
        res["arms"]["ner_on" if arm else "ner_off"] = r
        print(f"\n== NER {'ON ' if arm else 'OFF'} (presidio_ner in registry: {r['has_presidio_ner']}) ==")
        for k, v in r["recall"].items():
            print(f"  {k:14} recall {v:<7} caught by {r['caught_by_detector'].get(k, {})}")
        print(f"  clean corpus fires: {r['clean_corpus']['fires']}/{r['clean_corpus']['sentences']}"
              + (f"  -> {[(f['id'], f['matched']) for f in r['clean_corpus']['detail']]}" if r['clean_corpus']['fires'] else ""))
        print(f"  latency ms p50/p95/p99: {r['latency_ms']['p50']} / {r['latency_ms']['p95']} / {r['latency_ms']['p99']}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
