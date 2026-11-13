"""Regenerate gallery PNGs from real synthetic Office samples, without AI APIs.

Run from repository root with the review sandbox image and local converter.
Temporary source files are isolated in a temporary directory. Previews are
committed assets; they are not user files and contain no private data.
"""
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path


def docker(directory, code):
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,size=256m", "--mount", f"type=bind,source={directory},target=/samples", "--entrypoint", "python3", "ai-gateway-sandbox:workspace-review", "-c", code], check=True)


if __name__ == "__main__":
    output = Path("apps/web-chat/public/templates").resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workspace-previews-") as temporary:
        root = Path(temporary)
        docker(root, '''
import sys,json
sys.path.insert(0,'/sandbox/scripts')
from office_templates import TEMPLATES,create
from pathlib import Path
for name,(kind,purpose,heads) in TEMPLATES.items():
 data={'title':purpose,'subtitle':'Sample content · Template preview','language':'en'}
 if kind in ('docx','pptx','pdf') and name!='pdf.invoice':
  data['sections']=[{'heading':h,'body':'Sample content for '+h.lower()+'. Replace with your own facts and decisions.','bullets':['A clear objective','Evidence supplied by your team']} for h in heads]
 elif name=='pdf.invoice':
  data.update(invoice_number='SAMPLE-001',customer='Sample customer',currency='THB',items=[{'description':'Sample service','quantity':2,'unit_price':1500},{'description':'Sample deliverable','quantity':1,'unit_price':2500}])
 elif name=='xlsx.budget': data['rows']=[{'item':'Research','planned':12000,'actual':10500},{'item':'Design','planned':18000,'actual':17200},{'item':'Delivery','planned':9000,'actual':9500}]
 elif name=='xlsx.tracker': data['rows']=[{'task':'Research','owner':'Team A','status':'Complete','due_date':'2026-11-01'},{'task':'Design','owner':'Team B','status':'In progress','due_date':'2026-11-15'}]
 else: data.update(columns=[{'key':'item','label':'Item','type':'text'},{'key':'amount','label':'Amount','type':'number'}],rows=[{'item':'Sample A','amount':120},{'item':'Sample B','amount':250}])
 create(name,data,'/samples/'+name+'.'+kind)
''')
        for source in root.iterdir():
            if source.suffix in (".docx", ".pptx", ".xlsx"):
                request = urllib.request.Request("http://127.0.0.1:32996/convert?to=pdf", data=source.read_bytes(), headers={"Content-Type": "application/octet-stream", "X-Filename": source.name})
                with urllib.request.urlopen(request, timeout=90) as response:
                    source.with_suffix(".pdf").write_bytes(response.read())
        docker(root, '''
from pathlib import Path
import csv
import pypdfium2 as pdfium
from PIL import Image,ImageDraw,ImageFont
for source in Path('/samples').glob('*.pdf'):
 doc=pdfium.PdfDocument(source);page=doc[0];im=page.render(scale=1).to_pil();im.thumbnail((640,640));im.save(source.with_suffix('.png'));page.close();doc.close()
for source in Path('/samples').glob('*.csv'):
 rows=list(csv.reader(source.open(encoding='utf-8-sig')))
 im=Image.new('RGB',(640,300),'white');draw=ImageDraw.Draw(im);font=ImageFont.truetype('/sandbox/scripts/fonts/Sarabun-Regular.ttf',22)
 for y,row in enumerate(rows):
  draw.rectangle((20,20+y*65,620,85+y*65),fill='#16324F' if y==0 else '#eef3f8')
  for x,value in enumerate(row):draw.text((35+x*300,35+y*65),value,font=font,fill='white' if y==0 else '#172B4D')
 im.save(source.with_suffix('.png'))
''')
        for png in root.glob("*.png"):
            (output / png.name).write_bytes(png.read_bytes())
        print(json.dumps({"previews": len(list(root.glob("*.png"))), "output": str(output)}))
