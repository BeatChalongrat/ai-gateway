"""doc-converter — Agent Market's LibreOffice conversion service.

A finished .docx / .xlsx / .pptx becomes a PDF by a real LibreOffice
conversion (HarfBuzz shaping, the image's Thai fonts), and an .xlsx written
by openpyxl comes back with every formula recalculated. Python stdlib only.

    GET  /health                   {"status":"ok","soffice":"<version>"}
    POST /convert?to=pdf|xlsx      raw file body, name in X-Filename (its
                                   extension picks the filter) -> the bytes
    POST /check[?first_png=1]      raw PDF body -> {"pages", "text" (-layout),
                                   "raw_text" (content order), "page_png_sizes",
                                   "page_ink" (dark share per page),
                                   ["first_png_base64"]}

Every conversion runs in a fresh LibreOffice profile and a fresh work
directory under /tmp, both deleted afterwards; one heavy job (conversion or
check) runs at a time; each has a wall-clock limit after which its whole
process group is killed (504). Errors are short JSON reasons, never a trace.

Env: CONVERTER_PORT (8096), CONVERT_TIMEOUT_SECONDS (60),
     MAX_INPUT_BYTES (25 MB), QUEUE_WAIT_SECONDS (= timeout),
     MAX_PENDING (4 requests waiting or running), CHECK_MAX_PAGES (50).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip() or default)
        return v if v > 0 else default
    except ValueError:
        return default


PORT = _env_int("CONVERTER_PORT", 8096)
TIMEOUT = _env_int("CONVERT_TIMEOUT_SECONDS", 60)
MAX_INPUT = _env_int("MAX_INPUT_BYTES", 25 * 1024 * 1024)
QUEUE_WAIT = _env_int("QUEUE_WAIT_SECONDS", TIMEOUT)
MAX_PENDING = _env_int("MAX_PENDING", 4)
CHECK_MAX_PAGES = _env_int("CHECK_MAX_PAGES", 50)
TMP = os.environ.get("CONVERTER_TMP", "/tmp")
SOFFICE = os.environ.get("SOFFICE_BIN", "soffice")

# Extension -> PDF export filter: the extension decides, never detection.
_PDF_FILTERS = {
    "docx": "writer_pdf_Export", "doc": "writer_pdf_Export", "odt": "writer_pdf_Export",
    "rtf": "writer_pdf_Export",
    "xlsx": "calc_pdf_Export", "xls": "calc_pdf_Export", "ods": "calc_pdf_Export",
    "pptx": "impress_pdf_Export", "ppt": "impress_pdf_Export", "odp": "impress_pdf_Export",
}
_XLSX_INPUTS = {"xlsx", "xls", "ods"}
_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# The body must be the container its extension names (else LibreOffice's type
# detection would happily import any bytes as plain text), and the OOXML
# formats are loaded with their own import filter, never a detected one.
_ZIP, _OLE2 = b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAGIC = {"docx": _ZIP, "xlsx": _ZIP, "pptx": _ZIP, "odt": _ZIP, "ods": _ZIP, "odp": _ZIP,
          "doc": _OLE2, "xls": _OLE2, "ppt": _OLE2, "rtf": b"{\\rtf"}
_IN_FILTERS = {"docx": "MS Word 2007 XML", "xlsx": "Calc MS Excel 2007 XML",
               "pptx": "Impress MS PowerPoint 2007 XML"}
# A ZIP is not enough (a forced filter turns a foreign package into an empty
# page): the package must declare the main part its extension names.
_OOXML_MAIN = {"docx": "wordprocessingml.document.main+xml", "xlsx": "spreadsheetml.sheet.main+xml",
               "pptx": "presentationml.presentation.main+xml"}
_ODF_MIME = {"odt": "application/vnd.oasis.opendocument.text",
             "ods": "application/vnd.oasis.opendocument.spreadsheet",
             "odp": "application/vnd.oasis.opendocument.presentation"}


def _content_matches(ext: str, data: bytes) -> bool:
    if not data.startswith(_MAGIC[ext]):
        return False
    if ext not in _OOXML_MAIN and ext not in _ODF_MIME:
        return True
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("[Content_Types].xml" if ext in _OOXML_MAIN else "mimetype")
            if info.file_size > 1 << 20:
                return False
            part = z.read(info).decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, OSError, EOFError, RuntimeError, ValueError, NotImplementedError):
        return False
    return _OOXML_MAIN[ext] in part if ext in _OOXML_MAIN else part.strip() == _ODF_MIME[ext]


# Recalculate every formula on load (0 = always) for OOXML and ODF: openpyxl
# writes inputs and formulas but never the cached values. Macros never run.
_REGISTRY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
</oor:items>
"""

_heavy = threading.Lock()  # soffice is not safe concurrently; checks share it
_pending = threading.BoundedSemaphore(MAX_PENDING)
_version = "unknown"


class Failure(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _log(msg: str) -> None:
    sys.stderr.write(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()) + msg + "\n")
    sys.stderr.flush()


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run(argv: list[str], env: dict, timeout: float, cwd: str | None = None) -> tuple[int, bytes, bytes]:
    """Run in its own process group; on timeout kill the whole group -> 504."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        raise Failure(504, f"timed out after {int(timeout)}s")
    finally:
        # Nothing the job started may outlive it (soffice forks soffice.bin).
        _kill_group(proc)
    return proc.returncode, out, err


def _job_env(home: str) -> dict:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": home,
        "TMPDIR": home,
        "XDG_CACHE_HOME": os.path.join(home, ".cache"),
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SAL_USE_VCLPLUGIN": "svp",
    }


class _Job:
    """A fresh work directory and LibreOffice profile, removed on exit."""

    def __enter__(self):
        tag = uuid.uuid4().hex
        self.dir = os.path.join(TMP, f"job-{tag}")
        self.profile = os.path.join(TMP, f"lo-profile-{tag}")
        for d in (self.dir, os.path.join(self.dir, "in"), os.path.join(self.dir, "out"),
                  os.path.join(self.dir, "home"), os.path.join(self.profile, "user")):
            os.makedirs(d, mode=0o700, exist_ok=True)
        with open(os.path.join(self.profile, "user", "registrymodifications.xcu"), "w", encoding="utf-8") as f:
            f.write(_REGISTRY)
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.profile, ignore_errors=True)
        return False


def _soffice_argv(job: _Job, convert_to: str, src: str, infilter: str | None) -> list[str]:
    argv = [SOFFICE, f"-env:UserInstallation=file://{job.profile}", "--headless", "--invisible", "--norestore",
            "--nologo", "--nolockcheck", "--nodefault", "--nofirststartwizard"]
    if infilter:
        argv.append(f"--infilter={infilter}")
    return argv + ["--convert-to", convert_to, "--outdir", os.path.join(job.dir, "out"), src]


def convert(name: str, data: bytes, to: str) -> tuple[bytes, str]:
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    if to == "pdf":
        if ext not in _PDF_FILTERS:
            raise Failure(415, f"cannot convert .{ext or '?'} to pdf")
        convert_to = f"pdf:{_PDF_FILTERS[ext]}"
    elif to == "xlsx":
        if ext not in _XLSX_INPUTS:
            raise Failure(415, f"cannot convert .{ext or '?'} to xlsx")
        convert_to = "xlsx:Calc Office Open XML"
    else:
        raise Failure(400, "to must be pdf or xlsx")
    if not _content_matches(ext, data):
        raise Failure(415, f"content is not a .{ext} file")
    with _Job() as job:
        # A fixed, safe name: the caller's file name never reaches argv or a path.
        src = os.path.join(job.dir, "in", f"document.{ext}")
        with open(src, "wb") as f:
            f.write(data)
        argv = _soffice_argv(job, convert_to, src, _IN_FILTERS.get(ext))
        code, _out, err = _run(argv, _job_env(os.path.join(job.dir, "home")), TIMEOUT)
        dst = os.path.join(job.dir, "out", f"document.{to}")
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            detail = _short(err) or f"exit {code}"
            raise Failure(422, f"conversion produced no output ({detail})")
        with open(dst, "rb") as f:
            return f.read(), _CONTENT_TYPES[to]


def _png_size(path: str) -> dict:
    with open(path, "rb") as f:
        head = f.read(24)
    w, h = struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    return {"width": w, "height": h, "bytes": os.path.getsize(path)}


def _ink(path: str) -> float:
    """The share of a page's pixels that are not white — ink (a binary PGM from pdftoppm -gray), 0..1.
    A blank page is 0; anti-aliased text at a low resolution is grey, so any non-white pixel counts."""
    with open(path, "rb") as f:
        data = f.read()
    fields, i = [], 0
    while len(fields) < 4:                      # magic, width, height, maxval
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            i = data.index(b"\n", i) + 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        fields.append(data[i:j])
        i = j
    if fields[0] != b"P5":
        raise Failure(422, "unexpected page render")
    w, h, maxval = int(fields[1]), int(fields[2]), int(fields[3])
    px = data[i + 1:i + 1 + w * h]
    if maxval > 255 or not px:
        raise Failure(422, "unexpected page render")
    dark = sum(1 for b in px if b < maxval * 9 // 10)
    return round(dark / len(px), 5)


def check(pdf: bytes, first_png: bool) -> dict:
    if not pdf.startswith(b"%PDF-"):
        raise Failure(415, "body is not a PDF")
    with _Job() as job:
        src = os.path.join(job.dir, "in", "document.pdf")
        with open(src, "wb") as f:
            f.write(pdf)
        env = _job_env(os.path.join(job.dir, "home"))
        deadline = time.monotonic() + TIMEOUT

        def left() -> float:
            return max(1.0, deadline - time.monotonic())

        code, out, err = _run(["pdfinfo", src], env, left())
        m = re.search(rb"^Pages:\s+(\d+)", out, re.M)
        if code != 0 or not m:
            raise Failure(422, f"not a readable PDF ({_short(err) or 'pdfinfo failed'})")
        pages = int(m.group(1))
        code, text, err = _run(["pdftotext", "-layout", "-enc", "UTF-8", src, "-"], env, left())
        if code != 0:
            raise Failure(422, f"pdftotext failed ({_short(err)})")
        last = min(pages, CHECK_MAX_PAGES)
        png_dir = os.path.join(job.dir, "out")
        code, _o, err = _run(["pdftoppm", "-r", "72", "-png", "-f", "1", "-l", str(last), src,
                              os.path.join(png_dir, "page")], env, left())
        if code != 0:
            raise Failure(422, f"pdftoppm failed ({_short(err)})")
        pngs = sorted((p for p in os.listdir(png_dir) if p.endswith(".png")),
                      key=lambda p: int(re.sub(r"\D", "", p) or 0))
        # AM2 gate: the text in content-stream order (a table cell's lines stay together, which
        # -layout interleaves), and how much of each page is ink — a blank or black page is caught.
        code, raw_text, err = _run(["pdftotext", "-raw", "-enc", "UTF-8", src, "-"], env, left())
        if code != 0:
            raise Failure(422, f"pdftotext failed ({_short(err)})")
        ink_dir = os.path.join(job.dir, "ink")
        os.makedirs(ink_dir, exist_ok=True)
        code, _o, err = _run(["pdftoppm", "-r", "36", "-gray", "-f", "1", "-l", str(last), src,
                              os.path.join(ink_dir, "page")], env, left())
        if code != 0:
            raise Failure(422, f"pdftoppm failed ({_short(err)})")
        pgms = sorted((p for p in os.listdir(ink_dir) if p.endswith(".pgm")),
                      key=lambda p: int(re.sub(r"\D", "", p) or 0))
        result = {"pages": pages, "text": text.decode("utf-8", "replace"),
                  "raw_text": raw_text.decode("utf-8", "replace"),
                  "rendered_pages": len(pngs),
                  "page_png_sizes": [_png_size(os.path.join(png_dir, p)) for p in pngs],
                  "page_ink": [_ink(os.path.join(ink_dir, p)) for p in pgms]}
        if first_png and pngs:
            with open(os.path.join(png_dir, pngs[0]), "rb") as f:
                result["first_png_base64"] = base64.b64encode(f.read()).decode("ascii")
        return result


def _short(b: bytes | str, n: int = 160) -> str:
    s = b.decode("utf-8", "replace") if isinstance(b, bytes) else b
    s = " ".join(s.split())
    return s[:n]


def soffice_version() -> str:
    try:
        with _Job() as job:
            argv = [SOFFICE, f"-env:UserInstallation=file://{job.profile}", "--headless", "--version"]
            _code, out, _err = _run(argv, _job_env(os.path.join(job.dir, "home")), 60)
        line = out.decode("utf-8", "replace").strip().splitlines()
        return line[0].strip() if line else "unknown"
    except Exception as e:  # noqa: BLE001 - health must still answer
        _log(f"soffice --version failed: {type(e).__name__}")
        return "unknown"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "doc-converter"
    sys_version = ""

    def log_message(self, fmt, *args):  # the per-request line is written by _done
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict, close: bool = False):
        extra = {"Connection": "close"} if close else None
        if close:
            self.close_connection = True
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _done(self, status: int, started: float, size: int = 0):
        _log(f"{self.command} {urlparse(self.path).path} {status} {int((time.monotonic() - started) * 1000)}ms in={size}")

    def do_GET(self):
        started = time.monotonic()
        if urlparse(self.path).path == "/health":
            self._json(200, {"status": "ok", "soffice": _version})
            return self._done(200, started)
        self._json(404, {"error": "not found"})
        self._done(404, started)

    def _read_body(self) -> bytes:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise Failure(411, "Content-Length required")
        try:
            n = int(raw)
        except ValueError:
            raise Failure(400, "bad Content-Length")
        if n < 0:
            raise Failure(400, "bad Content-Length")
        if n > MAX_INPUT:
            # Drain a moderately oversized body so the client can read the 413;
            # anything larger is refused on the spot and the connection closed.
            if n <= 4 * MAX_INPUT:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    left -= len(chunk)
            raise Failure(413, f"input larger than {MAX_INPUT} bytes")
        if n == 0:
            raise Failure(400, "empty body")
        data = self.rfile.read(n)
        if len(data) != n:
            raise Failure(400, "truncated body")
        return data

    def do_POST(self):
        started = time.monotonic()
        url = urlparse(self.path)
        q = parse_qs(url.query)
        size = 0
        if url.path not in ("/convert", "/check"):
            self._json(404, {"error": "not found"}, close=True)
            return self._done(404, started)
        if not _pending.acquire(blocking=False):
            self._json(503, {"error": "busy: too many requests waiting"}, close=True)
            return self._done(503, started)
        try:
            data = self._read_body()
            size = len(data)
            if not _heavy.acquire(timeout=QUEUE_WAIT):
                raise Failure(503, "busy: another conversion is still running")
            try:
                if url.path == "/convert":
                    to = (q.get("to") or ["pdf"])[0].lower()
                    name = self.headers.get("X-Filename") or ""
                    body, ctype = convert(os.path.basename(name.replace("\\", "/")), data, to)
                    self._send(200, body, ctype)
                else:
                    first = (q.get("first_png") or ["0"])[0] in ("1", "true", "yes")
                    self._json(200, check(data, first))
            finally:
                _heavy.release()
            self._done(200, started, size)
        except Failure as f:
            self._json(f.status, {"error": f.reason}, close=f.status in (400, 411, 413))
            self._done(f.status, started, size)
        except Exception as e:  # noqa: BLE001 - never a trace to the caller
            _log(f"internal error: {type(e).__name__}")
            self._json(500, {"error": "internal error"}, close=True)
            self._done(500, started, size)
        finally:
            _pending.release()


def main() -> int:
    global _version
    _version = soffice_version()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    _log(f"doc-converter on :{PORT} ({_version}); timeout={TIMEOUT}s max_input={MAX_INPUT}")

    def stop(*_):
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
