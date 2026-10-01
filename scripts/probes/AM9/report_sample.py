"""AM9 report evidence, made offline in the sandbox image: the financial report sample (.docx) rendered
by the self-test, saved for the converter to print. Same mounts as print_samples.py; writes /out/report.docx."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path[0:0] = ["/src", "/sandbox/scripts"]
OUT = os.environ.get("AM9_OUT", "/out")

from agent_market.tests import samplekit  # noqa: E402

res = samplekit.render_sample("financial_report", out_dir=tempfile.mkdtemp(), formats=["docx"])
shutil.copy(res["paths"]["docx"], os.path.join(OUT, os.environ.get("AM9_NAME", "report.docx")))
print("done")
