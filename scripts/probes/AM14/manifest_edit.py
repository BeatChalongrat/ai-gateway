"""Small edits of a words file (agent.json / engine/words.json) that keep its layout: a sentence made from
moved ones (never retyped), a key dropped, an English reworded."""
import io
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


class Manifest:
    def __init__(self, path):
        self.path = path
        self.raw = io.open(path, encoding="utf-8").read()
        self.before = json.loads(self.raw)

    @property
    def now(self):
        return json.loads(self.raw)

    def th(self, key):
        return self.now["strings"][key]

    def en(self, key):
        m = self.now
        return m["en"][m["strings"][key]]

    @staticmethod
    def _line(k, v):
        return "    %s: %s" % (json.dumps(k, ensure_ascii=False), json.dumps(v, ensure_ascii=False))

    def _block(self, name):
        first = re.search(r'\n  "%s": \{\n' % name, self.raw).end()
        return first, self.raw.index("\n  }", first - 1) + 1

    def _add(self, name, k, v):
        first, close = self._block(name)
        body = self.raw[first:close].rstrip("\n")
        body = (body + ("" if body.endswith(",") else ",") + "\n") if body.strip() else ""
        self.raw = self.raw[:first] + body + self._line(k, v) + "\n" + self.raw[close:]

    def _drop(self, name, k, v):
        first, close = self._block(name)
        body = self.raw[first:close]
        line = self._line(k, v)
        assert body.count(line) == 1, (name, k)
        body = body.replace(line + ",\n", "").replace(",\n" + line + "\n", "\n").replace(line + "\n", "")
        self.raw = self.raw[:first] + body + self.raw[close:]

    def add(self, key, thai, english):
        """A sentence whose Thai is made from moved ones."""
        m = self.now
        assert key not in m["strings"], key
        assert thai not in m["en"] or m["en"][thai] == english, (key, m["en"].get(thai))
        self._add("strings", key, thai)
        if thai not in m["en"]:
            self._add("en", thai, english)

    def add_plain(self, key, text):
        """Words with no Thai in them (“VAT”): said as they are in both languages, so they have no English."""
        assert key not in self.now["strings"], key
        assert not re.search("[฀-๿]", text), text
        self._add("strings", key, text)

    def by_key(self, key, english):
        """The English of one key whose Thai is said another way under another key ("en_by_key")."""
        assert key in self.now["strings"], key
        if "en_by_key" not in self.now:
            end = self.raw.rstrip().rindex("}")
            self.raw = self.raw[:end].rstrip("\n") + ',\n  "en_by_key": {\n  }\n}\n'
        assert key not in self.now["en_by_key"], key
        self._add("en_by_key", key, english)

    def drop(self, key):
        m = self.now
        thai = m["strings"][key]
        self._drop("strings", key, thai)
        if thai not in self.now["strings"].values():
            self._drop("en", thai, m["en"][thai])

    def reword(self, key, english):
        m = self.now
        thai = m["strings"][key]
        old = self._line(thai, m["en"][thai])
        first, close = self._block("en")
        body = self.raw[first:close]
        assert body.count(old) == 1, key
        self.raw = self.raw[:first] + body.replace(old, self._line(thai, english)) + self.raw[close:]

    def save(self):
        after = self.now
        rest = lambda m: {k: v for k, v in m.items() if k not in ("strings", "en", "en_by_key")}  # noqa: E731
        assert rest(after) == rest(self.before), "something outside strings/en changed"
        io.open(self.path, "w", encoding="utf-8", newline="\n").write(self.raw)
        print(self.path, "·", len(after.get("strings") or {}), "keys ·", len(after["en"]), "English entries")


def edit(path, replacements, check_no=None):
    """Exact replacements in a source file: (old, new[, count])."""
    s = io.open(path, encoding="utf-8").read()
    for r in replacements:
        old, new, n = (r + (1,))[:3]
        assert s.count(old) == n, (path, old[:80], s.count(old))
        s = s.replace(old, new)
    import ast

    ast.parse(s)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print(path, "·", len(replacements), "edits")
