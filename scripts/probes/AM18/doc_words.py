#!/usr/bin/env python
"""AM18 — move a document's Thai words into a manifest, by script, so that not one of them is retyped.

The AM14 tool (scripts/probes/AM14/words_tool.py) moves the sentences a function says: each becomes
W("key", place=…). A document writer also keeps words at module level — the headings of its tables, the
names of its sheets, the labels of its face, tables of codes and their words — which are made when the
module is imported, before the step has said the run's language. Those become KEYS in the code (the
writer says them when it reads them: W.heads, W.table, W(key)), and this tool writes them to the manifest
the same way.

    list  <file.py>                                   as the AM14 tool's list
    apply <file.py> <decisions.json> --words <agent.json> [--call W]
          decisions.json:  { "<id>": ["key", "English sentence"] }                 → W("key", place=…)
                           { "<id>": ["key", "English sentence", {"_key": true}] } → "key"   (said later)
                           { "<id>": ["key", "English sentence", {"_strip": true}] }
                           { "<id>": ["key", "English sentence", {"old_place": "new_place"}] }
          a decision may name several ids at once: "<id>|<id>|<id>"

A string not named stays where it is. The Thai is never typed: it is read from the code.
"""
import ast
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AM14"))
import words_tool as T  # noqa: E402

THAI = T.THAI


def cmd_apply(path, decisions_path, words_path, call):
    src = io.open(path, encoding="utf-8").read()
    raw_decisions = json.load(io.open(decisions_path, encoding="utf-8"))
    decisions = {}
    for ids, d in raw_decisions.items():
        for i in ids.split("|"):
            decisions[i.strip()] = d
    raw = io.open(words_path, encoding="utf-8").read()
    manifest = json.loads(raw)
    strings, english = dict(manifest.get("strings") or {}), dict(manifest.get("en") or {})
    by_key = dict(manifest.get("en_by_key") or {})
    if "en_by_key" not in manifest:
        # the English kept by key goes after "en", as engine/words.json keeps it
        first, close = T.block(raw, "en")
        end = raw.index("\n", close)
        raw = raw[:end] + ',\n  "en_by_key": {\n  }' + raw[end:]
        raw = raw.replace("  },\n  \"en_by_key\"", "  },\n  \"en_by_key\"") if raw.count('"en_by_key"') == 1 else raw
        manifest = json.loads(raw)
        assert manifest["en_by_key"] == {}
    by_id = {e["id"]: e for e in T.entries(src)}
    missing = [i for i in decisions if i not in by_id]
    if missing:
        sys.exit("no such string (the file changed since `list`?): " + ", ".join(missing))
    new_strings, new_english, new_by_key, edits = [], [], [], []
    for i, d in decisions.items():
        e = by_id[i]
        key, en = d[0], d[1]
        opts = dict(d[2]) if len(d) > 2 else {}
        as_key = bool(opts.pop("_key", False))
        strip = bool(opts.pop("_strip", False))
        # the same Thai said another way in English for this one key (a heading beside a sentence's clause)
        keyed = bool(opts.pop("_by_key", False))
        rename = opts
        blocking = [f for f in e["flags"] if f.startswith(("a Thai string inside", "a format spec", "the span"))]
        if blocking:
            sys.exit(f"{i}: {blocking[0]} — move it by hand")
        text, places = e["text"], dict(e["places"])
        if as_key and places:
            sys.exit(f"{i}: a string with places cannot be kept as a key")
        for old, new in rename.items():
            if old not in places:
                sys.exit(f"{i}: no place {{{old}}} in {text!r}")
            text = re.sub(r"\{%s(?=[!:}])" % re.escape(old), "{" + new, text)
            places = {(new if k == old else k): v for k, v in places.items()}
        if strip:
            text = text.strip()
        want = sorted(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)[!:}]", text)) if places else []
        got = sorted(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)[!:}]", en)) if places else []
        if places and sorted(set(want)) != sorted(set(got)):
            sys.exit(f"{i} ({key}): the English has places {sorted(set(got))}, the Thai {sorted(set(want))}")
        if THAI.search(re.sub(r"“[^”]*”", "", en)):
            sys.exit(f"{i} ({key}): Thai in the English: {en!r}")
        if key in strings and strings[key] != text:
            sys.exit(f"{i}: the key {key!r} already says something else: {strings[key]!r}")
        if text in english and english[text] != en and not keyed:
            sys.exit(f"{i} ({key}): this Thai already has another English: {english[text]!r} (say it with _by_key, or use that one)")
        if keyed and not (text in english and english[text] != en):
            sys.exit(f"{i} ({key}): _by_key, but the Thai has no other English to differ from")
        if key not in strings:
            strings[key] = text
            new_strings.append((key, text))
        if keyed:
            if key in by_key and by_key[key] != en:
                sys.exit(f"{i} ({key}): already kept by key as {by_key[key]!r}")
            if key not in by_key:
                by_key[key] = en
                new_by_key.append((key, en))
        elif text in english:
            pass
        elif THAI.search(text):
            english[text] = en
            new_english.append((text, en))
        if as_key:
            edits.append((e["start"], e["end"], json.dumps(key)))
        else:
            args = "".join(f", {k}={T.as_written(v)}" for k, v in places.items())
            edits.append((e["start"], e["end"], f"{call}({json.dumps(key)}{args})"))
    for start, end, text in sorted(edits, reverse=True):
        src = src[:start] + text + src[end:]
    ast.parse(src)
    raw = T.add_entries(T.add_entries(T.add_entries(raw, "strings", new_strings), "en", new_english), "en_by_key", new_by_key)
    after = json.loads(raw)
    assert after["strings"] == strings and after["en"] == english and after["en_by_key"] == by_key, "the manifest did not come out as meant"
    kept = ("strings", "en", "en_by_key")
    assert {k: v for k, v in after.items() if k not in kept} == {k: v for k, v in manifest.items() if k not in kept}
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    io.open(words_path, "w", encoding="utf-8", newline="\n").write(raw)
    print(f"{path}: {len(edits)} strings moved · {len(new_strings)} new keys · {len(new_english)} new English entries · "
          f"{len(new_by_key)} by key")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "list":
        return T.cmd_list(a[1])
    if len(a) >= 5 and a[0] == "apply" and a[3] == "--words":
        call = a[6] if len(a) >= 7 and a[5] == "--call" else "W"
        return cmd_apply(a[1], a[2], a[4], call)
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
