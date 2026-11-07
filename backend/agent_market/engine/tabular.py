"""Read an uploaded spreadsheet into plain grids.

A grid keeps every cell exactly as the file holds it (numbers stay numbers,
text stays text) together with the Excel row number and column letter, so a
citation can point back at 'GL_2569-09.xlsx · Sheet1 · G412'. CSV files are
decoded with a Thai-aware fallback chain (UTF-8 BOM → UTF-8 → CP874/TIS-620),
because Excel on Thai Windows saves CSV as CP874 and a wrong guess produces
mojibake that every later step would analyse with confidence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from dataclasses import dataclass, field

MAX_ROWS = 200_000
MAX_COLS = 80


def col_letter(idx: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


@dataclass
class Sheet:
    name: str
    rows: list[list] = field(default_factory=list)  # rows[i] is Excel row i+1

    def cell(self, r: int, c: int):
        if r < len(self.rows) and c < len(self.rows[r]):
            return self.rows[r][c]
        return None

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass
class Workbook:
    file_name: str
    sha256: str
    size: int
    kind: str  # xlsx | csv
    sheets: list[Sheet]
    encoding: str | None = None
    error: str | None = None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _trim(rows: list[list]) -> list[list]:
    out = []
    for r in rows[:MAX_ROWS]:
        r = list(r[:MAX_COLS])
        while r and (r[-1] is None or (isinstance(r[-1], str) and not r[-1].strip())):
            r.pop()
        out.append(r)
    while out and not out[-1]:
        out.pop()
    return out


def _decode_csv(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for enc in ("cp874", "tis-620"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def _csv_value(text: str):
    t = text.strip()
    if t == "":
        return None
    plain = t.replace(",", "")
    neg = plain.startswith("(") and plain.endswith(")")
    if neg:
        plain = plain[1:-1]
    try:
        if plain.count(".") <= 1 and plain.lstrip("-").replace(".", "", 1).isdigit():
            num = float(plain) if "." in plain else int(plain)
            return -num if neg else num
    except ValueError:
        pass
    return t


def read_workbook(path: str, display_name: str | None = None) -> Workbook:
    name = display_name or os.path.basename(path)
    size = os.path.getsize(path)
    digest = sha256_file(path)
    ext = os.path.splitext(name)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True)
            sheets = []
            for ws in wb.worksheets:
                rows = [list(r) for r in ws.iter_rows(values_only=True)]
                sheets.append(Sheet(ws.title, _trim(rows)))
            wb.close()
            return Workbook(name, digest, size, "xlsx", sheets)
        if ext in (".csv", ".txt"):
            with open(path, "rb") as f:
                raw = f.read()
            text, enc = _decode_csv(raw)
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                delim = dialect.delimiter
            except csv.Error:
                delim = ","
            reader = csv.reader(io.StringIO(text), delimiter=delim)
            rows = [[_csv_value(c) for c in r] for r in reader]
            base = os.path.splitext(name)[0]
            return Workbook(name, digest, size, "csv", [Sheet(base[:31] or "csv", _trim(rows))], encoding=enc)
        return Workbook(name, digest, size, ext.lstrip("."), [], error="unsupported_type")
    except Exception as exc:  # noqa: BLE001 — an unreadable file is a finding, not a crash
        return Workbook(name, digest, size, ext.lstrip("."), [], error=f"unreadable: {type(exc).__name__}: {exc}"[:300])
