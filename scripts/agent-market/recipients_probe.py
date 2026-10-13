"""AM16 — nothing goes to a recipient that is not named, asked of the TEST stack's real gateway and of the real providers.

One harmless sentence (nothing of anyone's), one key (the test stack's Agent Market runner), and per leg a
model and a list of recipients. For each leg: what the gateway answered, and — read from its own request
log — whether a provider was called at all.

    python scripts/agent-market/recipients_probe.py

The legs (the list is ``metadata.allowed_recipients``; "-" = the key is not sent):
  * an outside model, the list names a company that runs it    → answered
  * Claude with Amazon named, GPT with Microsoft named         → answered (those are who runs them on this account)
  * Claude with only its maker named, GPT likewise             → 502: the router's account lets only endpoints that
                                                                 keep nothing through, and it is NOT re-routed
  * a model, the list names companies that cannot run it       → 400 no_allowed_provider, no provider called
  * the list names the route only · is empty · is null         → 400 (null is an empty list, never "did not ask")
  * the router's own "auto", an open model hosted by whoever   → 400: no company can be said to run it
  * a direct provider, the list names it                       → answered
  * the in-house model, the list is empty                      → answered (nothing leaves)
  * an outside model, no list                                  → answered as before
What OpenRouter did with the pin (only the company's own endpoints, no fallback, no endpoint that keeps
what it is sent) is read from its answer: the provider it names. No key is printed.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# PROBE_TARGET=live: only the legs that send nothing outside — the refusals, and the in-house model — asked of
# LIVE's gateway with live's own runner key (read from the settings file by name, never printed).
LIVE = os.environ.get("PROBE_TARGET", "test") == "live"
SSL = None
if LIVE:
    import ssl

    KEY = ""
    with io.open(os.path.join(ROOT, ".env"), encoding="utf-8") as _f:
        for _line in _f:
            if _line.startswith("AGENT_RUNNER_GATEWAY_API_KEY="):
                KEY = _line.split("=", 1)[1].strip().strip('"')
    if not KEY:
        sys.exit("the runner's gateway key is not set")
    GATEWAY = os.environ.get("PROBE_GATEWAY", "https://localhost:8080")
    SSL = ssl.create_default_context(cafile=os.path.join(ROOT, "infra", "caddy", "certs", "rootCA.pem"))
else:
    KEY = io.open(os.path.join(ROOT, "scripts/probes/AM1/results/runner_key.test"), encoding="utf-8").read().strip()
    GATEWAY = os.environ.get("PROBE_GATEWAY", "http://localhost:18080")
PG = os.environ.get("PROBE_PG", "ai-gateway-postgres-1" if LIVE else "sran-test-postgres-1")
ABSENT = object()

LEGS = [
    ("google/gemini-2.5-flash", ["openrouter:google"], 200),
    ("anthropic/claude-sonnet-4.5", ["openrouter:amazon"], 200),      # the company that runs it on this account
    ("anthropic/claude-sonnet-4.5", ["openrouter:anthropic"], 502),   # its maker's own endpoint: the router's account refuses it
    ("openai/gpt-4o-mini", ["openrouter:microsoft"], 200),
    ("openai/gpt-4o-mini", ["openrouter:openai"], 502),
    ("anthropic/claude-sonnet-4.5", ["openrouter:microsoft", "openrouter:openai"], 400),
    ("google/gemini-2.5-flash", ["openrouter:anthropic", "openrouter:amazon"], 400),
    ("google/gemini-2.5-flash", ["openrouter"], 400),
    ("google/gemini-2.5-flash", ["google"], 400),
    ("google/gemini-2.5-flash", [], 400),
    ("google/gemini-2.5-flash", None, 400),
    ("openrouter/auto", ["openrouter:google", "openrouter:anthropic", "openrouter:amazon", "openrouter:openai", "openrouter"], 400),
    ("deepseek/deepseek-chat", ["openrouter:deepseek", "openrouter"], 400),
    ("meta-llama/llama-3.3-70b-instruct", ["openrouter:meta", "openrouter"], 400),
    ("deepseek-v4-flash", ["deepseek"], 200),
    ("deepseek-v4-flash", ["openrouter:deepseek"], 400),
    ("qwen3.8-27b-fp8", [], 200),
    ("google/gemini-2.5-flash", ABSENT, 200),
]


def sql(q: str) -> str:
    r = subprocess.run(["docker", "exec", PG, "psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


def leg(model: str, allowed, want: int) -> dict:
    meta = {} if allowed is ABSENT else {"allowed_recipients": allowed}
    body = json.dumps({"model": model, "stream": False, "max_tokens": 400, "reasoning": {"mode": "off"}, "metadata": meta,
                       # (a sentence of its own for every leg: the gateway keeps answers for an hour, and a kept
                       # answer is no answer to "was this provider called")
                       "messages": [{"role": "user", "content": f"Reply with the one word: ok ({time.time_ns()})"}]}).encode("utf-8")
    req = urllib.request.Request(GATEWAY + "/v1/chat/completions", data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json", "X-Sran-Fallback": "off"})
    out = {"model": model, "list": "-" if allowed is ABSENT else json.dumps(allowed), "want": want}
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240, context=SSL) as r:
            j = json.loads(r.read().decode("utf-8"))
            out["status"] = r.status
            out["answer"] = " ".join(((j["choices"][0]["message"].get("content")) or "").split())[:20]
            out["gateway_provider"] = r.headers.get("X-Gateway-Provider") or j.get("provider")
            out["answered_by"] = j.get("model")
            out["request_id"] = j.get("id")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        out["status"] = e.code
        try:
            err = json.loads(text).get("error") or {}
            out["code"] = err.get("code")
            out["reason"] = (err.get("details") or {}).get("reason")
            out["request_id"] = err.get("request_id") or json.loads(text).get("request_id")
        except Exception:  # noqa: BLE001
            out["code"] = text[:120]
    out["seconds"] = round(time.time() - started, 1)
    out["ok"] = out["status"] == want
    return out


def models() -> int:
    """`recipients_probe.py models` — what the gateway's catalogue says of each model: its recipient."""
    req = urllib.request.Request(GATEWAY + "/v1/models", headers={"Authorization": "Bearer " + KEY, "X-Client-Name": "agent-runner"})
    with urllib.request.urlopen(req, timeout=30, context=SSL) as r:
        data = json.loads(r.read().decode("utf-8"))["data"]
    for m in data:
        vision = bool((m.get("capabilities") or {}).get("vision"))
        print(f"{m['id']:38s} route={str(m.get('provider')):11s} in-house={str(bool(m.get('self_hosted'))):5s} reads images={str(vision):5s} "
              f"recipients={', '.join(m.get('recipients') or []) or '(none)'}")
    return 0


def main() -> int:
    if sys.argv[1:2] == ["models"]:
        return models()
    bad = 0
    rows = []
    # on live: nothing is asked of a provider outside — only what must be refused, and the in-house model
    legs = [l for l in LEGS if l[2] == 400 or l[0] == "qwen3.8-27b-fp8"] if LIVE else LEGS
    for model, allowed, want in legs:
        r = leg(model, allowed, want)
        rows.append(r)
        bad += 0 if r["ok"] else 1
        tail = (f"answered “{r.get('answer')}” by {r.get('answered_by')} via {r.get('gateway_provider')}" if r["status"] == 200
                else f"{r.get('code')} · {r.get('reason')}")
        print(f"{'PASS' if r['ok'] else 'FAIL'} {model:28s} list={r['list']:62s} → {r['status']} (want {want}) · {tail} · {r['seconds']} s")
        time.sleep(1.5)
    out = os.path.join(ROOT, "scripts", "probes", "AM16", "results", "recipients")
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(out, f"gateway_probe_{'live' if LIVE else 'test'}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    refused = [r for r in rows if r["status"] == 400]
    held = [r for r in rows if r["status"] == 502]
    print(f"{len(rows) - bad} of {len(rows)} legs as expected · {len(refused)} refused before anything was sent · "
          f"{len(held)} failed at the router rather than go to a company that was not named")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
