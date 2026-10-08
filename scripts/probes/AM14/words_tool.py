#!/usr/bin/env python
"""AM14 — move a file's Thai sentences into a manifest, by script, so that not one of them is retyped.

    list  <file.py>                       every Thai string the code holds (not its docstrings), with an id,
                                          where it stands, and — for an f-string — the sentence with its {places}
    apply <file.py> <decisions.json> --words <agent.json | engine/words.json> [--call W]
                                          the strings named in the decisions leave the code: each becomes
                                          W("key", place=expression…), its Thai goes into "strings" exactly as
                                          the code had it, its English into "en"

decisions.json:  { "<id>": ["key", "English sentence"], … }
                 { "<id>": ["key", "English sentence", {"old_place": "new_place"}] }   (a place renamed)
                 { "<id>": ["key", "English sentence", {"_strip": true}] }             (the Thai without the
                                                                                        spaces at its ends)
A string not named stays where it is. The Thai is never typed: it is read from the code.

An id is L<line>:<column> of the string as `list` printed it; `apply` refuses a file that changed since.
"""
import ast
import io
import json
import keyword
import re
import sys

THAI = re.compile(r"[฀-๿]")
READS = {"index", "startswith", "endswith", "find", "replace", "split", "count", "strip", "lstrip", "rstrip", "get", "pop", "setdefault"}


def docstrings(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def has_thai(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and bool(THAI.search(node.value))
    return any(isinstance(v, ast.Constant) and isinstance(v.value, str) and THAI.search(v.value) for v in node.values)


def name_for(expr, taken):
    """A place's name, from the expression that fills it."""
    def base(e):
        if isinstance(e, ast.Name):
            return e.id
        if isinstance(e, ast.Attribute):
            return e.attr
        if isinstance(e, ast.Subscript) and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, str):
            return e.slice.value
        if isinstance(e, ast.Call):
            if isinstance(e.func, ast.Name) and e.func.id == "len" and len(e.args) == 1:
                return "n"
            if len(e.args) >= 1:
                return base(e.args[0])
        if isinstance(e, (ast.UnaryOp,)):
            return base(e.operand)
        return ""
    n = re.sub(r"[^a-z0-9_]", "_", (base(expr) or "").lower()).strip("_")
    if not n or n[0].isdigit():
        n = "v"
    if keyword.iskeyword(n) or n in ("key", "self"):
        n += "_"
    src = ast.unparse(expr)
    k, i = n, 1
    while k in taken and taken[k] != src:
        i += 1
        k = f"{n}{i}"
    taken[k] = src
    return k


def sentence(node):
    """(template, {place: expression source}, problems) of a string node."""
    if isinstance(node, ast.Constant):
        return node.value, {}, []
    parts, taken, problems = [], {}, []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value).replace("{", "{{").replace("}", "}}"))
            continue
        inner = [n for n in ast.walk(v.value) if isinstance(n, (ast.Constant, ast.JoinedStr)) and has_thai(n)]
        if inner:
            problems.append("a Thai string inside a {place}: " + ast.unparse(v.value)[:60])
        spec = ""
        if v.format_spec is not None:
            if all(isinstance(s, ast.Constant) for s in v.format_spec.values):
                spec = ":" + "".join(str(s.value) for s in v.format_spec.values)
            else:
                problems.append("a format spec that is itself computed")
        conv = {-1: "", 115: "!s", 114: "!r", 97: "!a"}[v.conversion]
        parts.append("{" + name_for(v.value, taken) + conv + spec + "}")
    return "".join(parts), taken, problems


def as_written(expr):
    """An expression in the code's own quotes (ast.unparse writes 'single' ones)."""
    if '"' in expr or "\\" in expr:
        return expr
    return re.sub(r"'([^']*)'", lambda m: '"' + m.group(1) + '"', expr)


def offset(lines, lineno, col):
    """Character offset in the whole text of (1-based line, UTF-8 byte column)."""
    line = lines[lineno - 1]
    return sum(len(x) for x in lines[:lineno - 1]) + len(line.encode("utf-8")[:col].decode("utf-8"))


def entries(src):
    tree = ast.parse(src)
    docs = docstrings(tree)
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p
    inside = set()   # the constants that are parts of an f-string are the f-string's
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            for v in ast.walk(n):
                if v is not n:
                    inside.add(id(v))
    lines = src.splitlines(keepends=True)
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Constant, ast.JoinedStr)) or id(n) in docs or id(n) in inside or not has_thai(n):
            continue
        # where it stands
        p, func, top = parents.get(id(n)), "", True
        q = n
        while id(q) in parents:
            q = parents[id(q)]
            if isinstance(q, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                top = False
                if not func and not isinstance(q, ast.Lambda):
                    func = q.name
        flags = []
        if top:
            flags.append("MODULE-LEVEL")
        if isinstance(p, ast.Dict) and any(k is n for k in p.keys):
            flags.append("DICT-KEY")
        if isinstance(p, ast.Compare):
            flags.append("COMPARED")
        if isinstance(p, ast.Subscript) and p.slice is n:
            flags.append("INDEX")
        if isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr in READS and n in p.args:
            flags.append("READ:" + p.func.attr)
        if isinstance(p, ast.Attribute):
            flags.append("METHOD:" + p.attr)
        call = ""
        if isinstance(p, ast.Call) and n in p.args:
            call = ast.unparse(p.func)[:30] + f"(arg {p.args.index(n)})"
        elif isinstance(p, ast.keyword):
            call = (p.arg or "**") + "="
        elif isinstance(p, ast.Dict):
            i = [k for k, v in zip(p.keys, p.values) if v is n]
            call = "{" + (ast.unparse(i[0])[:24] if i and i[0] is not None else "") + ": }"
        text, places, problems = sentence(n)
        start, end = offset(lines, n.lineno, n.col_offset), offset(lines, n.end_lineno, n.end_col_offset)
        seg = src[start:end]
        try:
            again = ast.parse("(" + seg + ")", mode="eval").body   # (in brackets: a string may run over lines)
            same = ast.dump(again) == ast.dump(n)
        except SyntaxError:
            same = False
        if not same:
            problems.append("the span could not be checked — move by hand")
        out.append({"id": f"L{n.lineno}:{n.col_offset}", "line": n.lineno, "func": func, "call": call, "flags": flags + problems,
                    "text": text, "places": places, "start": start, "end": end})
    out.sort(key=lambda e: e["start"])
    return out


def cmd_list(path):
    src = io.open(path, encoding="utf-8").read()
    for e in entries(src):
        head = f"{e['id']:<10} {e['func'] or '(module)':<22} {e['call']:<28}"
        flags = (" [" + ", ".join(e["flags"]) + "]") if e["flags"] else ""
        print(f"{head}{flags}\n    {e['text']}")
        if e["places"]:
            print("    " + " · ".join(f"{k}={v[:50]}" for k, v in e["places"].items()))


def block(raw, name):
    """(start of the first entry line, index of the closing brace line) of the top-level object `name`."""
    m = re.search(r'\n  "%s": \{\n' % re.escape(name), raw)
    if not m:
        return None
    return m.end(), raw.index("\n  }", m.end() - 1) + 1


def add_entries(raw, name, pairs):
    """`pairs` appended to the top-level flat object `name` (which exists), one entry a line."""
    if not pairs:
        return raw
    first, close = block(raw, name)
    body = raw[first:close]
    lines = "".join("    %s: %s,\n" % (json.dumps(k, ensure_ascii=False), json.dumps(v, ensure_ascii=False)) for k, v in pairs)
    lines = lines[:-2] + "\n"
    if body.strip():
        body = body.rstrip("\n")
        body = (body if body.endswith(",") else body + ",") + "\n"
    return raw[:first] + body + lines + raw[close:]


def cmd_apply(path, decisions_path, words_path, call):
    src = io.open(path, encoding="utf-8").read()
    decisions = json.load(io.open(decisions_path, encoding="utf-8"))
    raw = io.open(words_path, encoding="utf-8").read()
    manifest = json.loads(raw)
    if "strings" not in manifest:
        m = re.search(r'\n  "en": \{\n', raw)
        if not m:
            sys.exit(f"{words_path}: no top-level \"en\" to put \"strings\" before")
        raw = raw[:m.start()] + '\n  "strings": {\n  },' + raw[m.start():]
        manifest = json.loads(raw)
    strings, english = dict(manifest.get("strings") or {}), dict(manifest.get("en") or {})
    by_id = {e["id"]: e for e in entries(src)}
    missing = [i for i in decisions if i not in by_id]
    if missing:
        sys.exit("no such string (the file changed since `list`?): " + ", ".join(missing))
    new_strings, new_english, edits = [], [], []
    for i, d in decisions.items():
        e = by_id[i]
        key, en = d[0], d[1]
        rename = dict(d[2]) if len(d) > 2 else {}
        strip = bool(rename.pop("_strip", False))
        blocking = [f for f in e["flags"] if f.startswith(("a Thai string inside", "a format spec", "the span"))]
        if blocking:
            sys.exit(f"{i}: {blocking[0]} — move it by hand")
        text, places = e["text"], dict(e["places"])
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
        if text in english and english[text] != en:
            sys.exit(f"{i} ({key}): this Thai already has another English: {english[text]!r}")
        if key not in strings:
            strings[key] = text
            new_strings.append((key, text))
        if text not in english:
            english[text] = en
            new_english.append((text, en))
        args = "".join(f", {k}={as_written(v)}" for k, v in places.items())
        edits.append((e["start"], e["end"], f"{call}({json.dumps(key)}{args})"))
    for start, end, text in sorted(edits, reverse=True):
        src = src[:start] + text + src[end:]
    ast.parse(src)
    raw = add_entries(add_entries(raw, "strings", new_strings), "en", new_english)
    after = json.loads(raw)
    assert after["strings"] == strings and after["en"] == english, "the manifest did not come out as meant"
    assert {k: v for k, v in after.items() if k not in ("strings", "en")} == {k: v for k, v in manifest.items() if k not in ("strings", "en")}
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    io.open(words_path, "w", encoding="utf-8", newline="\n").write(raw)
    print(f"{path}: {len(edits)} strings moved · {len(new_strings)} new keys · {len(new_english)} new English entries")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "list":
        return cmd_list(a[1])
    if len(a) >= 5 and a[0] == "apply" and a[3] == "--words":
        call = a[6] if len(a) >= 7 and a[5] == "--call" else "W"
        return cmd_apply(a[1], a[2], a[4], call)
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
