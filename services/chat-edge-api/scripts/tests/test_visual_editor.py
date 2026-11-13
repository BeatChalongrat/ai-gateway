import io
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import visual_editor as visual


class VisualEditorTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def inspect(self,path):return visual.execute(path,{"mode":"inspect"},self.root/"unused")

    def test_word_styles_preserve_unchanged_runs_and_block_order(self):
        from docx import Document
        source=self.root/"source.docx";doc=Document();doc.add_paragraph("First");doc.add_table(rows=1,cols=1).cell(0,0).text="Table";doc.add_paragraph("Last");doc.save(source)
        info=self.inspect(source);self.assertEqual([b["kind"] for b in info["blocks"]],["paragraph","table","paragraph"])
        target=self.root/"edited.docx"
        visual.execute(source,{"mode":"edit","sha256":info["sha256"],"preserve_text":True,"changes":[{"id":info["fields"][0]["id"],"value":"First","style":{"size":22,"bold":True,"color":"#123456","align":"center"}}]},target)
        result=Document(target);self.assertEqual(result.paragraphs[0].runs[0].font.size.pt,22);self.assertTrue(result.paragraphs[0].runs[0].bold);self.assertEqual(result.paragraphs[1].text,"Last")
        with self.assertRaisesRegex(ValueError,"preserve text"):
            visual.execute(source,{"mode":"edit","sha256":info["sha256"],"preserve_text":True,"changes":[{"id":info["fields"][0]["id"],"value":"Invented"}]},self.root/"bad.docx")

    def test_slide_geometry_and_clean_background(self):
        from pptx import Presentation
        from pptx.util import Inches
        source=self.root/"source.pptx";deck=Presentation();shape=deck.slides.add_slide(deck.slide_layouts[6]).shapes.add_textbox(Inches(1),Inches(1),Inches(5),Inches(2));shape.text="Title";deck.save(source)
        info=self.inspect(source);field=info["fields"][0]
        self.assertEqual(Presentation(self.root/"background.pptx").slides[0].shapes[0].text,"")
        visual.execute(source,{"mode":"edit","sha256":info["sha256"],"changes":[{"id":field["id"],"value":"New title","style":{"size":30},"box":{"left":36,"top":36,"width":360,"height":144}}]},self.root/"edited.pptx")
        result=Presentation(self.root/"edited.pptx").slides[0].shapes[0]
        self.assertEqual(result.text,"New title");self.assertEqual(result.left/12700,36)

    def test_pdf_replaces_native_text_without_hiding_old_text(self):
        from reportlab.pdfgen import canvas
        from pypdf import PdfReader
        source=self.root/"source.pdf";c=canvas.Canvas(str(source));c.drawString(72,700,"Old sensitive phrase");c.drawString(72,650,"Keep this text searchable");c.save()
        info=self.inspect(source);field=next(f for f in info["fields"] if "Old sensitive" in f["value"])
        self.assertTrue(info["background"].startswith("data:image/png"))
        visual.execute(source,{"mode":"edit","sha256":info["sha256"],"changes":[{"id":field["id"],"value":"Replacement","box":{"left":72,"top":field["box"]["top"],"width":250,"height":30},"style":{"color":"#112233","size":12}}]},self.root/"edited.pdf")
        text=PdfReader(self.root/"edited.pdf").pages[0].extract_text()
        self.assertNotIn("Old sensitive phrase",text);self.assertIn("Replacement",text);self.assertIn("Keep this text searchable",text)
        self.assertIn("Old sensitive phrase",PdfReader(source).pages[0].extract_text())

    def test_image_preserves_alpha_and_rejects_invalid_controls(self):
        from PIL import Image
        source=self.root/"source.png";image=Image.new("RGBA",(100,80),(100,20,30,128));image.save(source);info=self.inspect(source)
        visual.execute(source,{"mode":"edit","sha256":info["sha256"],"image":{"rotation":90,"contrast":1.2}},self.root/"edited.png")
        result=Image.open(self.root/"edited.png");self.assertEqual(result.size,(80,100));self.assertEqual(result.getpixel((0,0))[3],128)
        with self.assertRaises(ValueError):visual.image_edit(source.read_bytes(),{"image":{"crop":{"left":90,"top":0,"width":50,"height":30}}})

    def test_invalid_style_and_stale_fingerprint_refused(self):
        for values in ({"size":float("nan")},{"size":0},{"color":"url(http://bad)"},{"html":"<script>"}):
            with self.assertRaises(ValueError):visual.style(values)
        from PIL import Image
        source=self.root/"source.png";Image.new("RGB",(50,50)).save(source)
        with self.assertRaisesRegex(ValueError,"Source changed"):visual.execute(source,{"mode":"edit","sha256":"bad"},self.root/"bad.png")

    def test_render_all_pages_and_refuse_partial_ai_coverage(self):
        from reportlab.pdfgen import canvas
        for count in (2,9):
            source=self.root/f"pages{count}.pdf";c=canvas.Canvas(str(source))
            for i in range(count):c.drawString(72,700,f"Page {i}");c.showPage()
            c.save()
            if count==2:
                result=visual.render_pdf(source.read_bytes());self.assertEqual(len(result["pages"]),2)
                self.assertTrue(all(p["image"].startswith("data:image/jpeg") for p in result["pages"]))
            else:
                with self.assertRaisesRegex(ValueError,"complete documents"):visual.render_pdf(source.read_bytes())
                self.assertEqual(visual.render_pdf(source.read_bytes(),8)["page_count"],9)

    def test_word_embedded_picture_is_previewed_and_not_deleted_by_text_edit(self):
        from docx import Document
        from docx.shared import Inches
        from PIL import Image
        image=self.root/"pic.png";Image.new("RGB",(40,40),"red").save(image)
        doc=Document();p=doc.add_paragraph("Keep picture");p.add_run().add_picture(str(image),width=Inches(1))
        source=self.root/"source.docx";doc.save(source);info=self.inspect(source)
        self.assertEqual([b["kind"] for b in info["blocks"]],["paragraph","image"])
        with self.assertRaisesRegex(ValueError,"drawings or fields"):
            visual.execute(source,{"mode":"edit","sha256":info["sha256"],"changes":[{"id":info["fields"][0]["id"],"value":"Replace"}]},self.root/"bad.docx")

    def test_image_inspection_reports_original_dimensions(self):
        from PIL import Image
        source=self.root/"large.png";Image.new("RGBA",(1600,800),(20,30,40,128)).save(source)
        info=self.inspect(source);self.assertEqual((info["width"],info["height"]),(1600,800));self.assertTrue(info["transparent"])

    def test_rotated_pdf_is_refused_and_scan_has_no_fake_editable_text(self):
        from reportlab.pdfgen import canvas
        from pypdf import PdfReader,PdfWriter
        from PIL import Image
        image=self.root/"scan.png";Image.new("RGB",(80,80),"gray").save(image)
        source=self.root/"scan.pdf";c=canvas.Canvas(str(source));c.drawImage(str(image),72,600);c.save()
        self.assertEqual(self.inspect(source)["fields"],[])
        writer=PdfWriter(clone_from=source);writer.pages[0].rotate(90);rotated=self.root/"rotated.pdf";writer.write(rotated)
        with self.assertRaisesRegex(ValueError,"rotated/cropped"):self.inspect(rotated)


if __name__=="__main__":unittest.main()
