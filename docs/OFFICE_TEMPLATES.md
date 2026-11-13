# Office templates and delivery checks

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Quality follow-up (2026-11-13): the shared gate now attempts bounded PPTX edge repairs and rerenders candidates; the preview has a visual paragraph/text-box editor, native PDF text replacement and immutable revisions. [AI review](VISUAL_EDITOR.md) adds bounded Word/PPT/image correction through the configured gateway. See [exact acceptance rules and limits](OFFICE_EDITOR.md).

Media companion (2026-11-13): `motion_templates.catalog()` exposes `motion.explainer`, `motion.metrics` and `motion.slideshow` in the same sandbox. They return silent MP4, poster, contact sheet and editable JSON. See [Office/media workflows](OFFICE_MEDIA_PARITY.md); these add three motion starters to the twelve Office starters below.

Source update: **2026-11-12**. This extends [Office workflows](OFFICE_WORKFLOWS.md)
for Web Chat and Electron Desktop. It improves generation and verification;
it is not a benchmark claim that a configured model equals Claude or ChatGPT.

## Shared workflow

1. Read the request and source files. Identify audience, format, language,
   existing template, data types, units and missing essential facts.
2. For a new document, inspect `office_templates.catalog()` and choose a starter.
   For an existing document, edit its original bytes instead of rebuilding it.
3. Populate supplied facts, evidence and references. Do not invent amounts,
   invoice details, dates, citations or business conclusions to fill a template.
4. Write the deliverable under `/job/out`, reopen it and inspect the report.
5. The shared Go quality stage checks Office artifacts before persistence.
   With the converter configured, recalculate XLSX formulas, render documents,
   and inspect every rendered page. Repair reported failures before delivery.
6. Review the actual preview and business figures. A successful render does
   not prove that every label fits or that the analysis is correct.

Both clients receive the same sandbox library and workflow instructions through
`run_python`. Web's Office context also describes when to use it instead of a
plan card. This is a model-callable catalog, not a new visual template picker.

## Catalog

| ID | Intended structure |
|---|---|
| `pptx.executive_brief` | Summary, evidence, recommendation, next steps; navy/gold |
| `pptx.project_update` | Progress, milestones, risks, decisions; teal |
| `pptx.proposal` | Problem, approach, scope, timeline, cost; violet |
| `docx.business_report` | Executive summary, findings, recommendations, sources |
| `docx.proposal` | Objective, scope, deliverables, timeline, commercial terms |
| `docx.meeting_minutes` | Attendees, discussion, decisions, actions |
| `pdf.business_report` | Paginated report with repeated table headers |
| `pdf.invoice` | Supplied invoice/customer/currency, line items, calculated total |
| `xlsx.budget` | Planned/actual amounts, variance formulas and totals |
| `xlsx.tracker` | Task, owner, status and date register |
| `xlsx.analysis` | Caller-defined typed columns and formulas |
| `csv.data_export` | UTF-8 BOM export, CSV quoting and literal text protection |

Report variants share layout code and recommend different outlines; they do not
automatically invent those sections. Presentation themes differ and long content
is split into bounded groups. Tables are paginated; overly wide/verbose slide
tables are rejected with guidance to use an appendix workbook.

## Python interface

The library ships inside the sandbox image at `/sandbox/scripts/office_templates.py`.
In either client's `run_python` tool:

```python
import sys
sys.path.insert(0, "/sandbox/scripts")
from office_templates import catalog, create, inspect_file
print(catalog())
```

Creation takes `(template_id, data, output_path)` and returns a structural report.
It refuses an existing output path or a mismatched extension. A fresh sandbox
per call means the same output filename can still be used for a later revision.

```python
print(create("xlsx.budget", {
    "title": "November budget",
    "rows": [
        {"item": "Engineering", "planned": 100, "actual": 80},
        {"item": "Support", "planned": 50, "actual": 60}
    ]
}, "/job/out/budget.xlsx"))
```

Numbers above are illustrative inputs, not defaults inserted by the library.
Budget rows require all amounts; missing values are not silently changed to zero.
The resulting variance is planned minus actual. Other business definitions
should use a custom analysis sheet and explicit formulas.

Documents and slides accept:

```python
data = {
    "title": "Project review",
    "language": "en",
    "sections": [{
        "heading": "Decision required",
        "body": "Use the facts supplied for this project.",
        "bullets": ["State the recommendation and its evidence."],
        "table": {"headers": ["Action", "Owner"],
                  "rows": [["Review", "Named project owner"]]},
        "sources": ["User-provided project record"],
        "notes": "Speaker notes for presentations"
    }]
}
print(create("pptx.executive_brief", data, "/job/out/review.pptx"))
```

Invoices require title, invoice_number, customer, currency and nonnegative
quantity/unit_price items. Decimal line amounts are rounded to two decimal
places and summed. Tax, discounts, currency conversion and legal invoice rules
are not inferred; this starter is not a tax-compliance engine.

## Spreadsheet and CSV contracts

Custom columns have `key`, optional `label`, and `type`:

- `text` (default): retains identifiers and leading zeroes; a string beginning
  with `=` remains a literal XLSX string.
- `number`: accepts finite numbers, rejects booleans/NaN/infinity.
- `date`: ISO `YYYY-MM-DD`, stored as an Excel date or ISO CSV text.
- `formula`: explicit XLSX formula beginning with `=`; not accepted for CSV.

Rows are objects keyed by declared columns. Unknown keys and duplicate column
keys fail instead of dropping data silently. XLSX uses filters, frozen headers,
readable widths, number formats, repeated print headers and landscape print setup.
CSV uses the standard writer to preserve commas, quotes and embedded newlines.
Potential spreadsheet formulas in text cells are prefixed with an apostrophe;
numeric negative values remain numbers. Consumers should import ID columns as
text: CSV itself cannot enforce Excel's import type inference.

`inspect_file()` reopens XLSX/DOCX/PPTX/PDF/CSV. It reports formula/cache issues,
page/slide counts, out-of-canvas shapes, or inconsistent CSV widths. It does not
prove formula semantics, text fit inside shapes, or Microsoft Office fidelity.

## Server quality stage

`internal/service/office_quality.go` is wired into `CodeInterpreter.Run`,
`DocService.runGenerator` and `ExcelService.runExecutor` before file persistence.
Therefore the `/code/run` Desktop API, streaming Web Chat Python tool and plan
cards share it. It applies to XLSX, DOCX, PPTX and PDF outputs; CSV verification
is currently provided by the template library, not every arbitrary Python script.

With `OFFICE_CONVERTER_URL` configured:

- XLSX is inspected with bounded ZIP/XML parsing. External workbook link parts
  and embedded macros are refused for automatic processing.
- Formula workbooks are recalculated by LibreOffice, then checked again for
  formula count, missing cached values and error cells such as `#DIV/0!`.
  This detects execution errors, not a logically incorrect business formula.
- Office documents are converted to PDF; PDFs go directly to the page checker.
  All pages must render. Blank-page and edge-content heuristics become warnings.
- Python results can include a PDF preview and first-page PNG. Original outputs
  have priority under the existing eight-artifact cap. Plan cards expose the
  report under `validation.office_quality`; they do not attach extra preview files.
- A converter failure or invalid workbook fails the affected deliverable and
  does not attach it as successful. Partial Python output may contain other,
  independently successful artifacts; read the exit status and diagnostics.

Without that URL, formula errors detectable in the original XLSX still fail,
but rendering/recalculation are reported as unavailable (`status: partial`).
The client must not interpret that as full verification. Historical plan cards
without a stored report do not acquire a verification badge on reload.

LibreOffice may normalize workbook formatting/features when saving a recalculated
copy. Macros, external links, advanced Excel functions, SmartArt and exact Office
layout compatibility are not certified. Source files remain unchanged.

Limits: 25 MiB per Office review, at most eight original artifacts per Python
run, 90 seconds per reviewed document and 120 seconds across a Python result.
The converter has its own 60-second subprocess limit and page cap (50 by default).
Desktop's code request allows 300 seconds for execution plus review; cancellation
still propagates. Oversize or incompletely rendered files fail with a reason.

## Deployment

The converter is optional and isolated on an internal network, with no published
port, no service secrets, a read-only root, dropped capabilities, resource caps
and a fresh non-root LibreOffice profile per conversion. The client rejects
public converter URLs, checks resolved connection addresses, ignores proxy env
and refuses redirects. Do not expose the unauthenticated converter publicly.

From the repository root, build the updated sandbox first:

```powershell
docker build -f services/chat-edge-api/sandbox/Dockerfile.sandbox -t ai-gateway-sandbox:latest services/chat-edge-api
docker compose -p ai-gateway -f docker-compose.webchat.yml -f docker-compose.office.yml up -d --build office-converter chat-edge-api web-chat
```

For the base/desktop deployment, use `docker-compose.yml` with the Office overlay
and `--profile desktop`; include the deployment's other existing overlays.
Never merge the base and standalone webchat files. Rebuild/package Electron
separately for its updated instructions and request deadline. Source changes
and review-tag images do not update an installed application automatically.

## Verification and comparison

The Python regression suite covers every catalog entry, real file reopen,
formulas, literal identifiers, Thai text, CSV quoting, invoice totals and failure
behavior. Go tests cover recalc errors, cache loss, external links, partial render,
unavailable configuration, artifact publication and plan report serialization.
Web tests verify that checked/partial/recalculated states are not conflated.
See [Office workflows](OFFICE_WORKFLOWS.md) for the final executed test record.

The design follows publicly documented expectations: [Anthropic's XLSX workflow](https://github.com/anthropics/skills/blob/main/skills/xlsx/SKILL.md)
emphasizes recalculation and checking results, while [ChatGPT Work](https://learn.chatgpt.com/)
describes refining source material into reviewable documents, spreadsheets and
presentations. These inform the workflow; they do not establish product parity
or model intelligence. Native UI and real-model task acceptance remain separate.
