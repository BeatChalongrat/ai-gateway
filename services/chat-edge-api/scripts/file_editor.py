"""Bounded structured file editing. Existing bytes are never overwritten.

CLI: file_editor.py source.ext request.json output.ext
request: {mode: inspect|edit|repair, sha256?, changes:[{id,value,type?}], sheet?}
"""
from __future__ import annotations
import csv
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import zipfile

MAX_BYTES = 16 * 1024 * 1024
MAX_FIELDS = 1000


def _read(path):
    raw = Path(path).read_bytes()
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("File must be nonempty and at most 16 MiB")
    if Path(path).suffix.lower() in (".docx", ".pptx", ".xlsx"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(f.file_size for f in entries) > 64*1024*1024:
                raise ValueError("Office archive exceeds inspection limits")
            if len({f.filename for f in entries}) != len(entries):
                raise ValueError("Duplicate Office archive parts")
            if any("vbaproject" in f.filename.lower() or "externallinks/" in f.filename.lower() for f in entries):
                raise ValueError("Macros and external workbook links are not supported by the editor")
    return raw


def _paragraph_targets(doc):
    def blocks(container, prefix):
        for i, p in enumerate(container.paragraphs):
            yield f"{prefix}/p/{i}", p
        for i, table in enumerate(container.tables):
            seen = set()
            for r, row in enumerate(table.rows):
                for c, cell in enumerate(row.cells):
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    yield from blocks(cell, f"{prefix}/table/{i}/{r}/{c}")
    yield from blocks(doc, "body")
    seen = set()
    for i, section in enumerate(doc.sections):
        for key in ("header", "footer", "first_page_header", "first_page_footer", "even_page_header", "even_page_footer"):
            part = getattr(section, key)
            if part.is_linked_to_previous or part._element in seen:
                continue
            seen.add(part._element)
            yield from blocks(part, f"section/{i}/{key}")


def _slide_targets(deck):
    def shapes(items, prefix):
        for shape in items:
            key = f"{prefix}/shape/{shape.shape_id}"
            if shape.has_text_frame:
                for j, p in enumerate(shape.text_frame.paragraphs):
                    yield f"{key}/p/{j}", p
            if shape.has_table:
                for r, row in enumerate(shape.table.rows):
                    for c, cell in enumerate(row.cells):
                        if cell.is_spanned:
                            continue
                        for j, p in enumerate(cell.text_frame.paragraphs):
                            yield f"{key}/cell/{r}/{c}/p/{j}", p
            if hasattr(shape, "shapes"):
                yield from shapes(shape.shapes, key)
    for i, slide in enumerate(deck.slides):
        yield from shapes(slide.shapes, f"slide/{i+1}")


def _set_paragraph(paragraph, text):
    # A complete paragraph replacement preserves its paragraph properties and
    # first run style; it cannot promise to preserve mixed inline formatting.
    style = deepcopy(paragraph.runs[0]._r.rPr) if paragraph.runs else None
    # Clearing only .runs leaves hyperlink/field text behind in DOCX. A full
    # paragraph replacement must also remove that content, retaining pPr.
    paragraph.clear()
    run = paragraph.add_run()
    run.text = text
    if style is not None:
        run._r.insert(0, style)


def _csv_rows(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp874")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) > 100000 or sum(len(r) for r in rows) > 1000000:
        raise ValueError("CSV exceeds editor limits")
    return rows


def _load(path, raw, request):
    ext = Path(path).suffix.lower()
    fields, targets, sheets = [], {}, []
    def add(key, value, kind="text", setter=None):
        if len(fields) < MAX_FIELDS:
            fields.append({"id":key,"value":str(value) if value is not None else "", "type":kind})
        if setter is not None:
            targets[key] = setter
    if ext == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(raw), data_only=False)
        sheets = book.sheetnames
        sheet = book[request.get("sheet") or sheets[0]]
        start = request.get("offset", 0)
        if type(start) is not int or not 0 <= start <= 1_000_000:
            raise ValueError("Invalid row offset")
        for row in sheet.iter_rows(min_row=start+1, max_row=min(start+50, max(20, sheet.max_row)), max_col=min(20, max(5, sheet.max_column))):
            for cell in row:
                if cell.__class__.__name__ == "MergedCell":
                    continue
                kind = "formula" if cell.data_type == "f" else "number" if isinstance(cell.value, (float,int)) and not isinstance(cell.value,bool) else "text"
                key = cell.coordinate
                add(key, cell.value, kind, cell)
        return fields, targets, sheets, book.save
    if ext in (".docx", ".pptx"):
        if ext == ".docx":
            from docx import Document
            document = Document(io.BytesIO(raw)); iterator = _paragraph_targets(document)
        else:
            from pptx import Presentation
            document = Presentation(io.BytesIO(raw)); iterator = _slide_targets(document)
        for key, p in iterator:
            add(key, p.text, setter=p)
            if len(targets) > MAX_FIELDS:
                raise ValueError("Document exceeds 1000 editable paragraphs; split it first")
        return fields, targets, sheets, document.save
    if ext == ".csv":
        rows = _csv_rows(raw)
        start = request.get("offset", 0)
        if type(start) is not int or not 0 <= start <= 100000:
            raise ValueError("Invalid row offset")
        for r in range(start, min(len(rows), start+50)):
            for c, value in enumerate(rows[r][:20]):
                add(f"R{r+1}C{c+1}", value, setter=(rows[r],c))
        def save(output):
            with open(output,"w",encoding="utf-8-sig",newline="") as dest:
                csv.writer(dest).writerows(rows)
        return fields, targets, sheets, save
    if ext == ".pdf":
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or len(reader.pages)>100:
            raise ValueError("Use an unencrypted PDF of at most 100 pages")
        notes = {}
        for i in range(len(reader.pages)):
            add(f"page/{i+1}/note", "", setter=i)
        def save(output):
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            font = Path(__file__).parent/"fonts/Sarabun-Regular.ttf"
            pdfmetrics.registerFont(TTFont("EditorSarabun",str(font)))
            writer=PdfWriter()
            for i,source_page in enumerate(reader.pages):
                page = writer.add_page(source_page)
                if i in notes:
                    overlay=io.BytesIO(); c=canvas.Canvas(overlay,pagesize=(float(page.mediabox.width),float(page.mediabox.height)))
                    c.setFont("EditorSarabun",10)
                    text=notes[i]
                    if "\n" in text or pdfmetrics.stringWidth(text,"EditorSarabun",10)>float(page.mediabox.width)-72:
                        raise ValueError("PDF note must be a single line that fits the page")
                    c.drawString(36,24,text);c.save()
                    page.merge_page(PdfReader(overlay).pages[0])
            writer.write(output)
        return fields, {key:(notes,value) for key,value in targets.items()}, sheets, save
    if ext in (".txt", ".md", ".json"):
        text = raw.decode("utf-8-sig")
        if len(raw)>1024*1024:
            raise ValueError("Text editor supports at most 1 MiB")
        box = [text]; add("content",text,setter=box)
        def save(output):
            if len(box[0].encode("utf-8"))>1024*1024: raise ValueError("Text editor supports at most 1 MiB")
            if ext==".json": json.loads(box[0])
            Path(output).write_text(box[0],encoding="utf-8")
        return fields, targets, sheets, save
    raise ValueError("Editor supports DOCX, PPTX, XLSX, CSV, PDF notes, TXT, Markdown and JSON")


def repair_pptx(source, output, attempt=1):
    from pptx import Presentation
    from pptx.util import Pt
    from pptx.enum.text import MSO_AUTO_SIZE
    deck=Presentation(source)
    before=[p.text for _,p in _slide_targets(deck)]
    changes=0
    margin=320040  # 0.35 inch: outside the converter's 0.3 inch edge strips.
    for slide in deck.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text:
                continue
            max_width=max(margin,deck.slide_width-2*margin)
            max_height=max(margin,deck.slide_height-2*margin)
            old=(shape.left,shape.top,shape.width,shape.height)
            shape.width=min(shape.width,max_width); shape.height=min(shape.height,max_height)
            shape.left=max(margin,min(shape.left,deck.slide_width-margin-shape.width))
            shape.top=max(margin,min(shape.top,deck.slide_height-margin-shape.height))
            shape.text_frame.word_wrap=True
            shape.text_frame.auto_size=MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            if old!=(shape.left,shape.top,shape.width,shape.height): changes+=1
            if attempt>1:
                for p in shape.text_frame.paragraphs:
                    for run in p.runs:
                        if run.font.size and run.font.size.pt>12:
                            run.font.size=Pt(max(12,run.font.size.pt*.85)); changes+=1
    if before != [p.text for _,p in _slide_targets(deck)]:
        raise ValueError("Layout repair changed text")
    deck.save(output)
    return {"changed_shapes_or_runs":changes,"text_preserved":True}


def execute(source, request, output):
    raw=_read(source); fingerprint=hashlib.sha256(raw).hexdigest()
    mode=request.get("mode","inspect")
    if mode=="repair":
        if Path(source).suffix.lower()!=".pptx": raise ValueError("Automatic repair supports PPTX only")
        return repair_pptx(source,output,request.get("attempt",1))
    fields,targets,sheets,save=_load(source,raw,request)
    if mode=="inspect":
        return {"sha256":fingerprint,"format":Path(source).suffix.lower().lstrip('.'),"fields":fields,"sheets":sheets,"offset":request.get("offset",0),"limit":MAX_FIELDS,"pdf_notes_only":Path(source).suffix.lower()==".pdf"}
    if mode!="edit" or request.get("sha256")!=fingerprint:
        raise ValueError("Source changed; reload before saving")
    changes=request.get("changes")
    if not isinstance(changes,list) or not 1<=len(changes)<=MAX_FIELDS:
        raise ValueError("Supply 1–1000 changes")
    if Path(output).exists(): raise ValueError("Output already exists")
    seen=set(); ext=Path(source).suffix.lower()
    for change in changes:
        key=change.get("id"); value=change.get("value"); kind=change.get("type","text")
        if key not in targets or key in seen or not isinstance(value,str) or len(value)>1000000:
            raise ValueError("Invalid, duplicate or oversized editor field")
        seen.add(key); target=targets[key]
        if ext==".xlsx":
            if kind=="number":
                number=float(value)
                if not math.isfinite(number): raise ValueError("Numbers must be finite")
                target.value=number
            elif kind=="formula":
                if not value.startswith('=') or '[' in value or any(x in value.upper() for x in ('WEBSERVICE(', 'HYPERLINK(', 'RTD(', '|')):
                    raise ValueError("Only local workbook formulas are allowed")
                target.value=value
            elif kind=="text": target.value=value; target.data_type='s'
            else: raise ValueError("Unsupported cell type")
        elif ext in (".docx",".pptx"): _set_paragraph(target,value)
        elif ext==".csv":
            if kind!="text": raise ValueError("CSV cells are text")
            target[0][target[1]]="'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        elif ext==".pdf": target[0][target[1]]=value
        else: target[0]=value
    save(output)
    return {"edited":len(changes),"source_sha256":fingerprint}


if __name__=="__main__":
    try:
        print(json.dumps(execute(sys.argv[1],json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")),sys.argv[3]),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error":str(exc)}));sys.exit(1)
