"""Visual Office/PDF/image editing inside the network-isolated sandbox.

No HTML or model-authored code is executed. All edits use bounded typed fields.
CLI: source request.json out/result.ext. render mode consumes an actual PDF.
"""
from __future__ import annotations
import base64
from copy import deepcopy
import ctypes
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys

import file_editor as legacy

EMU = 12700
STYLE_KEYS = {"size", "bold", "italic", "underline", "color", "align", "space_after"}


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid numeric editing control")
    return value


def style(value):
    if not isinstance(value, dict) or set(value) - STYLE_KEYS:
        raise ValueError("Unsupported style control")
    for key, val in value.items():
        if key in ("size", "space_after"): number(val, 6 if key == "size" else 0, 144)
        elif key in ("bold", "italic", "underline") and type(val) is not bool: raise ValueError("Invalid style flag")
        elif key == "color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(val)): raise ValueError("Invalid color")
        elif key == "align" and val not in ("left", "center", "right", "justify"): raise ValueError("Invalid alignment")
    return value


def rect(value, width, height):
    if not isinstance(value, dict) or set(value) != {"left", "top", "width", "height"}: raise ValueError("Invalid box")
    x = number(value["left"], 0, width); y = number(value["top"], 0, height)
    w = number(value["width"], 1, width); h = number(value["height"], 1, height)
    if x+w > width+.01 or y+h > height+.01: raise ValueError("Box is outside the page")
    return value


def uri(image, format="PNG"):
    output = io.BytesIO(); image.save(output, format=format)
    if output.tell() > 3*1024*1024: raise ValueError("Rendered page exceeds preview limit")
    return "data:image/"+("jpeg" if format=="JPEG" else "png")+";base64,"+base64.b64encode(output.getvalue()).decode()


def render_pdf(raw, page=None):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(raw)
    try:
        if len(doc)>100 or len(doc)<1: raise ValueError("Document page limit exceeded")
        indices = range(len(doc)) if page is None else [page]
        if page is None and len(doc)>8: raise ValueError("AI review supports complete documents up to 8 pages; split the source first")
        result=[]
        for i in indices:
            if type(i) is not int or not 0<=i<len(doc): raise ValueError("Invalid page")
            p=doc[i]
            try:
                scale=min(2,1280/max(p.get_size()))
                bitmap=p.render(scale=scale)
                try: result.append({"page":i,"width":p.get_width(),"height":p.get_height(),"image":uri(bitmap.to_pil().convert("RGB"),"JPEG")})
                finally: bitmap.close()
            finally:p.close()
        return {"pages":result,"page_count":len(doc)}
    finally: doc.close()


def font_style(p, ppt=False):
    run=p.runs[0] if p.runs else None
    font=run.font if run else None
    color="#222222"
    try:
        if font.color.rgb: color="#"+str(font.color.rgb)
    except (AttributeError, TypeError): pass
    alignment=int(p.alignment) if p.alignment is not None else (1 if ppt else 0)
    align=({1:"left",2:"center",3:"right",4:"justify"} if ppt else {0:"left",1:"center",2:"right",3:"justify"}).get(alignment,"left")
    inherited=[]
    if not ppt:
        current=p.style
        for _ in range(20):
            if current is None:break
            inherited.append(current.font);current=current.base_style
    def prop(name,default):
        for candidate in ([font] if font else [])+inherited:
            value=getattr(candidate,name,None)
            if value is not None:return value
        return default
    size=prop("size",None)
    return {"size":size.pt if size else (18 if ppt else 11),"bold":bool(prop("bold",False)),"italic":bool(prop("italic",False)),"underline":bool(prop("underline",False)),"color":color,"align":align}


def apply_style(p, values, ppt=False):
    from docx.shared import Pt as DocPt, RGBColor as DocRGB
    from pptx.util import Pt as SlidePt
    from pptx.dml.color import RGBColor as SlideRGB
    values=style(values); Pt=SlidePt if ppt else DocPt; RGB=SlideRGB if ppt else DocRGB
    if "align" in values:
        p.alignment=({"left":1,"center":2,"right":3,"justify":4} if ppt else {"left":0,"center":1,"right":2,"justify":3})[values["align"]]
    if "space_after" in values:
        if ppt: p.space_after=Pt(values["space_after"])
        else: p.paragraph_format.space_after=Pt(values["space_after"])
    if not p.runs: p.add_run()
    for run in p.runs:
        for key in ("bold","italic","underline"):
            if key in values: setattr(run.font,key,values[key])
        if "size" in values: run.font.size=Pt(values["size"])
        if "color" in values: run.font.color.rgb=RGB.from_string(values["color"][1:])


def word_model(raw):
    from docx import Document
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    doc=Document(io.BytesIO(raw))
    targets=dict(legacy._paragraph_targets(doc)); ids={p._p:key for key,p in targets.items()}
    fields=[{"id":key,"value":p.text,"style":font_style(p)} for key,p in targets.items()]
    if len(fields)>1000: raise ValueError("At most 1000 paragraphs are editable")
    def nodes(container):
        root=container.element.body if hasattr(container,"element") and hasattr(container.element,"body") else container._tc
        result=[]
        for element in root:
            if element.tag.endswith("}p") and element in ids:
                result.append({"kind":"paragraph","id":ids[element]})
                # Embedded images remain native in the saved DOCX. Show a safe
                # raster preview without fetching external relationships.
                from docx.oxml.ns import qn
                from PIL import Image
                for drawing in element.xpath('.//w:drawing'):
                    for blip in drawing.xpath('.//a:blip'):
                        rid=blip.get(qn('r:embed'))
                        if not rid:continue
                        part=doc.part.related_parts.get(rid)
                        if part is None:continue
                        try:
                            im=Image.open(io.BytesIO(part.blob))
                            if im.width*im.height>16_000_000:continue
                            im.thumbnail((1280,1280));im=im.convert('RGBA')
                            extent=drawing.xpath('.//wp:extent')
                            width=int(extent[0].get('cx'))/EMU if extent else im.width
                            result.append({"kind":"image","image":uri(im),"width":width})
                        except (OSError,ValueError):continue
            elif element.tag.endswith("}tbl"):
                table=Table(element,container)
                result.append({"kind":"table","rows":[[nodes(cell) for cell in row.cells] for row in table.rows]})
        return result
    section=doc.sections[0]
    model={"kind":"docx","fields":fields,"blocks":nodes(doc),"width":section.page_width.pt,"height":section.page_height.pt,
           "margins":{"left":section.left_margin.pt,"right":section.right_margin.pt,"top":section.top_margin.pt,"bottom":section.bottom_margin.pt}}
    return doc,targets,model


def slide_model(raw, selected):
    from pptx import Presentation
    doc=Presentation(io.BytesIO(raw));w=doc.slide_width/EMU;h=doc.slide_height/EMU
    if len(doc.slides)>100 or not 0<=selected<len(doc.slides): raise ValueError("Invalid slide or slide count")
    targets={};fields=[]
    for i,slide in enumerate(doc.slides):
        for shape in slide.shapes:
            # Native top-level text frames are editable. Groups/tables/charts stay
            # on the exact rendered background, never flattened into the source.
            if not shape.has_text_frame or abs(shape.rotation)>.001: continue
            key=f"slide/{i+1}/shape/{shape.shape_id}"
            targets[key]=shape
            frame=shape.text_frame
            fields.append({"id":key,"value":shape.text,"page":i,"box":{"left":shape.left/EMU,"top":shape.top/EMU,"width":shape.width/EMU,"height":shape.height/EMU},"style":font_style(frame.paragraphs[0],True),"insets":{side:getattr(frame,"margin_"+side)/EMU for side in ("left","top","right","bottom")},"vertical":{1:"top",3:"middle",4:"bottom"}.get(frame.vertical_anchor,"top")})
    if len(fields)>1000: raise ValueError("At most 1000 text frames are editable")
    return doc,targets,{"kind":"pptx","fields":fields,"width":w,"height":h,"page":selected,"page_count":len(doc.slides)}


def pdf_model(raw, selected):
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw))
    if reader.is_encrypted: raise ValueError("Encrypted PDF editing is unsupported")
    if reader.get_fields(): raise ValueError("PDF forms/signatures require a dedicated form editor")
    doc=pdfium.PdfDocument(raw)
    if len(doc)>100 or not 0<=selected<len(doc): doc.close();raise ValueError("Invalid page or page count")
    page=doc[selected]
    if page.get_rotation()!=0 or any(abs(x)>0.01 for x in page.get_bbox()[:2]):
        page.close();doc.close();raise ValueError("Normalize rotated/cropped PDF pages before editing")
    tp=page.get_textpage(); fields=[];targets={};w,h=page.get_size()
    for index,obj in enumerate(page.get_objects(max_depth=1,textpage=tp)):
        if obj.type!=pdfium.raw.FPDF_PAGEOBJ_TEXT: continue
        matrix=obj.get_matrix()
        if abs(matrix.b)>.001 or abs(matrix.c)>.001: continue
        text=obj.extract()
        if not text.strip(): continue
        left,bottom,right,top=obj.get_bounds()
        key=f"page/{selected+1}/text/{index}"
        red,green,blue,alpha=(ctypes.c_uint() for _ in range(4))
        pdfium.raw.FPDFPageObj_GetFillColor(obj,red,green,blue,alpha)
        fields.append({"id":key,"value":text,"page":selected,"box":{"left":left,"top":h-top,"width":max(1,right-left),"height":max(1,top-bottom)},"style":{"size":obj.get_font_size(),"bold":False,"italic":False,"underline":False,"color":f"#{red.value:02x}{green.value:02x}{blue.value:02x}","align":"left"}})
        targets[key]=(obj,matrix,top,left)
    tp.close()  # PDFium requires text handles closed before object removal.
    if len(fields)>1000: page.close();doc.close();raise ValueError("Page exceeds 1000 text objects")
    return doc,page,targets,{"kind":"pdf","fields":fields,"width":w,"height":h,"page":selected,"page_count":len(doc)}


def edits(request, model):
    changes=request.get("changes")
    if not isinstance(changes,list) or not 1<=len(changes)<=1000: raise ValueError("Supply 1–1000 changes")
    fields={f["id"]:f for f in model["fields"]};seen=set()
    for change in changes:
        if not isinstance(change,dict) or set(change)-{"id","value","style","box","type"}: raise ValueError("Invalid change")
        key=change.get("id");value=change.get("value")
        if key not in fields or key in seen or not isinstance(value,str) or len(value)>20000: raise ValueError("Unknown, duplicate or oversized text field")
        if request.get("preserve_text") and value!=fields[key]["value"]: raise ValueError("AI layout repair must preserve text exactly")
        seen.add(key)
        if "style" in change: style(change["style"])
        if "box" in change: rect(change["box"],model["width"],model["height"])
        yield change,fields[key]


def image_edit(raw, request):
    from PIL import Image,ImageEnhance,ImageOps
    image=Image.open(io.BytesIO(raw))
    if image.width*image.height>16_000_000 or getattr(image,"n_frames",1)>1: raise ValueError("Image must be static and at most 16 megapixels")
    image=ImageOps.exif_transpose(image).convert("RGBA")
    controls=request.get("image",{})
    if not isinstance(controls,dict) or set(controls)-{"rotation","brightness","contrast","crop"}: raise ValueError("Invalid image controls")
    if "crop" in controls:
        box=rect(controls["crop"],image.width,image.height)
        image=image.crop((box["left"],box["top"],box["left"]+box["width"],box["top"]+box["height"]))
    rotation=controls.get("rotation",0)
    if rotation not in (0,90,180,270): raise ValueError("Rotation must be 0, 90, 180 or 270")
    image=image.rotate(-rotation,expand=True)
    alpha=image.getchannel("A")
    image=ImageEnhance.Brightness(image).enhance(number(controls.get("brightness",1),.25,2))
    image=ImageEnhance.Contrast(image).enhance(number(controls.get("contrast",1),.25,2));image.putalpha(alpha)
    return image


def execute(source, request, output):
    raw=legacy._read(source);ext=Path(source).suffix.lower();mode=request.get("mode","inspect")
    selected=request.get("page",0)
    if type(selected) is not int or selected<0: raise ValueError("Invalid page")
    fingerprint=hashlib.sha256(raw).hexdigest()
    if mode=="render":return render_pdf(raw,None if request.get("all_pages") else selected)
    if mode not in ("inspect","edit"):raise ValueError("Unknown visual mode")
    if mode=="edit" and request.get("sha256")!=fingerprint: raise ValueError("Source changed; reload before editing")
    if mode=="edit" and Path(output).exists():raise ValueError("Output already exists")
    if ext in (".png",".jpg",".jpeg",".webp"):
        image=image_edit(raw,request if mode=="edit" else {})
        width,height=image.size
        transparent=image.getchannel("A").getextrema()[0]<255
        if mode=="edit":image.save(output,format="PNG")
        else:
            image.thumbnail((1280,1280))
        return {"kind":"image","sha256":fingerprint,"width":width,"height":height,"transparent":transparent,"fields":[],"pages":[{"page":0,"width":width,"height":height,"image":uri(image)}],"page_count":1}
    if ext==".docx":
        doc,targets,model=word_model(raw)
        if mode=="edit":
            for change,field in edits(request,model):
                if "box" in change:raise ValueError("Word paragraphs flow; use paragraph styles rather than absolute boxes")
                p=targets[change["id"]]
                if change["value"]!=field["value"]:
                    if p._p.xpath('.//w:drawing | .//w:pict | .//w:fldChar | .//w:fldSimple'):
                        raise ValueError("Text replacement in paragraphs containing drawings or fields is unsupported; formatting is still editable")
                    legacy._set_paragraph(p,change["value"])
                apply_style(p,change.get("style",{}))
            doc.save(output)
    elif ext==".pptx":
        doc,targets,model=slide_model(raw,selected)
        if mode=="edit":
            for change,field in edits(request,model):
                shape=targets[change["id"]]
                if change["value"]!=field["value"]: shape.text_frame.text=change["value"]
                for p in shape.text_frame.paragraphs:apply_style(p,change.get("style",{}),True)
                if "box" in change:
                    for key,attr in (("left","left"),("top","top"),("width","width"),("height","height")):setattr(shape,attr,int(change["box"][key]*EMU))
                shape.text_frame.word_wrap=True
            doc.save(output)
        else:
            for shape in targets.values():shape.text_frame.clear()
            doc.save(Path(output).with_name("background.pptx"))
    elif ext==".pdf":
        doc,page,targets,model=pdf_model(raw,selected)
        try:
            if mode=="inspect":
                for obj,*_ in targets.values():page.remove_obj(obj);obj.close()
                page.gen_content();bitmap=page.render(scale=min(2,1280/max(page.get_size())))
                try:model["background"]=uri(bitmap.to_pil())
                finally:bitmap.close()
            else:
                from reportlab.pdfgen import canvas
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                from pypdf import PdfReader,PdfWriter
                overlay=io.BytesIO();c=canvas.Canvas(overlay,pagesize=(model["width"],model["height"]))
                for name,file in (("VisualRegular","Sarabun-Regular.ttf"),("VisualBold","Sarabun-Bold.ttf")):
                    pdfmetrics.registerFont(TTFont(name,str(Path(__file__).parent/"fonts"/file)))
                for change,field in edits(request,model):
                    obj,matrix,oldtop,oldleft=targets[change["id"]]
                    box=change.get("box",field["box"]);s={**field["style"],**change.get("style",{})};font="VisualBold" if s.get("bold") else "VisualRegular"
                    if s.get("italic") or s.get("underline") or s.get("align","left")!="left":raise ValueError("PDF text supports size, bold and color; alignment is controlled by its box")
                    lines=change["value"].split("\n")
                    if len(lines)>1 and len(lines)*s["size"]*1.2>box["height"]+2:raise ValueError("Replacement text exceeds PDF box height")
                    if any(pdfmetrics.stringWidth(line,font,s["size"])>box["width"]+2 for line in lines):raise ValueError("Replacement text exceeds PDF box width; enlarge it or reduce the font")
                    page.remove_obj(obj);obj.close()
                    c.setFillColor(s["color"]);c.setFont(font,s["size"])
                    for j,line in enumerate(lines):c.drawString(box["left"],model["height"]-box["top"]-(oldtop-matrix.f)-j*s["size"]*1.2,line)
                c.save();page.gen_content();modified=io.BytesIO();doc.save(modified)
                # A fresh rewrite excludes orphaned old streams. Other page objects
                # remain vectors/text. This is editing, not certified redaction.
                writer=PdfWriter(clone_from=io.BytesIO(modified.getvalue()))
                writer.pages[selected].merge_page(PdfReader(overlay).pages[0]);writer.write(output)
        finally:page.close();doc.close()
    else:raise ValueError("Visual editor supports DOCX, PPTX, PDF, PNG, JPEG and WebP")
    model["sha256"]=fingerprint
    return model if mode=="inspect" else {"edited":len(request["changes"]),"source_sha256":fingerprint}


if __name__=="__main__":
    try:print(json.dumps(execute(sys.argv[1],json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")),sys.argv[3]),ensure_ascii=False))
    except Exception as exc:print(json.dumps({"error":str(exc)}));sys.exit(1)
