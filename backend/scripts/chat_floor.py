"""Chat-picker floor — vendor models are curated BEFORE they reach the
picker, the way the vendors' own pickers work (FIX65 Part 1).

Every model a vendor offers in its own chat picker reads images, calls
tools and streams, so no capability icon there ever flickers. Ours
showed the whole active catalogue (144 rows, FIX47.6's curation was
never applied) and hid the gaps with icons that turned grey.

The floor: a vendor row is offered in chat (``show_in_chat``) only when
the catalogue says vision + tools + streaming, and it is on the keep
list the operator hands in. Self-hosted providers are exempt — the
operator runs what they run, and the picker says what each of their
models can do instead of hiding it. ``is_active`` is never touched (the
API keeps every model), no row is deleted, and every change is one
switch the admin page can flip back.

    python -m scripts.chat_floor --keep keep.json            # dry run
    python -m scripts.chat_floor --keep keep.json --apply

``keep.json``: ``{"provider": ["model", ...], ...}``. A provider that is
not in the file is left untouched entirely (reported). A provider that
would end with NO shown chat model refuses to apply for that provider
(reported) — an empty provider means the floor is too strict for it or
its capability data is wrong, both of which a person decides.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models.model_config import ModelConfigORM  # noqa: E402
from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS  # noqa: E402


def is_media(row: ModelConfigORM) -> bool:
    return bool(row.supports_video_gen or row.supports_audio_gen or row.supports_tts)


def missing_for_floor(row: ModelConfigORM) -> list[str]:
    gaps = []
    if not row.supports_vision:
        gaps.append("vision")
    if not row.supports_tools:
        gaps.append("tools")
    if not row.supports_streaming:
        gaps.append("streaming")
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", required=True, help="JSON: {provider: [model, ...]}")
    ap.add_argument("--apply", action="store_true", help="write show_in_chat; default is a dry run")
    args = ap.parse_args()
    with open(args.keep, encoding="utf-8") as fh:
        keep: dict[str, list[str]] = json.load(fh)

    db = SessionLocal()
    try:
        rows = db.execute(select(ModelConfigORM).order_by(ModelConfigORM.provider, ModelConfigORM.model)).scalars().all()
        total_before = len(rows)
        by_provider: dict[str, list[ModelConfigORM]] = defaultdict(list)
        for r in rows:
            by_provider[r.provider].append(r)

        plan: list[tuple[ModelConfigORM, bool, str]] = []  # (row, show, reason)
        report: dict[str, dict] = {}
        for provider, prows in sorted(by_provider.items()):
            chat_rows = [r for r in prows if r.is_active and not is_media(r)]
            shown_before = sum(1 for r in chat_rows if r.show_in_chat)
            entry = report.setdefault(provider, {"active_chat": len(chat_rows), "shown_before": shown_before})
            if provider in SELF_HOSTED_PROVIDERS:
                entry["decision"] = "self-hosted: exempt from the floor; only listed hides apply"
                # The operator may still hide a named row (a test model).
                for r in chat_rows:
                    wanted = provider not in keep or r.model in keep[provider]
                    if r.show_in_chat and not wanted:
                        plan.append((r, False, "hidden by the keep list (self-hosted, no floor)"))
                continue
            if provider not in keep:
                entry["decision"] = "not in the keep list: untouched"
                continue
            wanted_names = set(keep[provider])
            unknown = sorted(n for n in wanted_names if n not in {r.model for r in prows})
            entry["keep_not_in_catalogue"] = unknown
            keep_fails = {}
            shown_after = 0
            for r in chat_rows:
                gaps = missing_for_floor(r)
                wanted = r.model in wanted_names
                if wanted and gaps:
                    keep_fails[r.model] = gaps
                show = wanted and not gaps
                if show:
                    shown_after += 1
                if show != bool(r.show_in_chat):
                    reason = (
                        "on the keep list and passes the floor" if show
                        else ("fails the floor: missing " + ", ".join(gaps) if gaps else "not on the keep list")
                    )
                    plan.append((r, show, reason))
            entry["keep_fails_floor"] = keep_fails
            entry["passes_floor_not_kept"] = sorted(r.model for r in chat_rows if not missing_for_floor(r) and r.model not in wanted_names)
            entry["shown_after"] = shown_after
            if shown_after == 0:
                entry["decision"] = "REFUSED: the provider would keep no chat model — floor too strict or capability data wrong"
                plan = [p for p in plan if p[0].provider != provider]
            else:
                entry["decision"] = "apply"

        print(json.dumps(report, indent=2, ensure_ascii=False))
        print()
        for r, show, reason in plan:
            print(f"{'SHOW' if show else 'HIDE'}  {r.provider:11s} {r.model:40s} {reason}")
        print(f"\n{len(plan)} change(s); rows total {total_before}")

        if not args.apply:
            print("dry run — nothing written")
            return 0
        for r, show, _ in plan:
            r.show_in_chat = show
        db.commit()
        after = db.execute(select(ModelConfigORM)).scalars().all()
        print(f"applied; rows total {len(after)} (before {total_before}); shown in chat now: "
              f"{sum(1 for r in after if r.is_active and r.show_in_chat)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
