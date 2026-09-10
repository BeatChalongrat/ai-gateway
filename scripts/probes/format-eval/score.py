import re
FENCE = re.compile(r"```[a-zA-Z0-9_-]*\r?\n(.*?)```", re.S)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", re.M)
NUMBERED = re.compile(r"^\s{0,3}\d+[.)]\s+\S", re.M)
BULLET = re.compile(r"^\s{0,3}[-*•]\s+\S", re.M)

def strip_code(t): return FENCE.sub(" ", t)

def dominant(text):
    """Deterministic precedence. 'bullets' is never a valid label — an answer
    that goes bulleted always scores a miss, which is the behaviour under test."""
    code_chars = sum(len(m.group(1)) for m in FENCE.finditer(text))
    body = len(re.sub(r"\s", "", text)) or 1
    if code_chars and len(re.sub(r"\s", "", "".join(m.group(1) for m in FENCE.finditer(text)))) / body > 0.40:
        return "code"
    outside = strip_code(text)
    if TABLE_SEP.search(outside) and len(TABLE_ROW.findall(outside)) >= 3:
        return "table"
    n, b = len(NUMBERED.findall(outside)), len(BULLET.findall(outside))
    if n >= 2 and n >= b: return "numbered"
    if b >= 2: return "bullets"
    return "prose"

def counts(text):
    outside = strip_code(text)
    return {"bullets": len(BULLET.findall(outside)),
            "tables": 1 if (TABLE_SEP.search(outside) and len(TABLE_ROW.findall(outside)) >= 3) else 0}

def thai_ratio(text):
    """Share of Thai among Thai+Latin letters, in prose only (code excluded)."""
    o = strip_code(text)
    th = len(re.findall(r"[\u0E00-\u0E7F]", o))
    la = len(re.findall(r"[A-Za-z]", o))
    return None if (th + la) == 0 else round(th / (th + la), 4)

def score(text, label):
    d = dominant(text)
    c = counts(text)
    return {"dominant": d, "match": d == label, "chars": len(text),
            "bullets": c["bullets"], "tables": c["tables"], "thai": thai_ratio(text)}
