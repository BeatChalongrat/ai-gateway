# AM-2 fixture pack

This is the ground truth that E2 extraction, Agent 1 (Invoice Processing), Agent 4 (Expense Audit) and the template filler are scored against. It was built in Phase A, before any engine code.

- **Generators:** `agent_market/tools/e2fix/`. Every expected value comes from a spec and never from the engine, so an eval compares two independent code paths.
- **Consistency test:** `agent_market/tests/test_am2_fixtures.py` checks that every golden file describes its document, with no engine involved.

Regenerate from `backend/` on a machine with Chrome. The generators overwrite their own sub-folders; this README is not generated.

```
python -m agent_market.tools.e2fix              # e2/  (invoices and receipts)
python -m agent_market.tools.e2fix.templates    # templates/
python -m agent_market.tools.e2fix.expense      # expense/
bash ../scripts/agent-market/python-lane.sh -k am2_fixtures
```

Directories named `fixtures`, `tests` and `tools` are left out of the engine zip that the runner ships into every sandbox step, so none of this reaches production.

## e2/: 24 Thai invoices and receipts

| id | class | what it tests |
|---|---|---|
| d01 | digital | Clean full tax invoice, modern layout. The **zero-question case.** |
| d02 | digital | Second clean layout: classic, Tahoma; the full company name (the modern layouts print บจก.); hyphenated tax IDs; DD/MM/BE date; branch 00002; header discount |
| d03 | digital | Abbreviated tax invoice (POS, 80 mm). No buyer, which is normal for this type; VAT included |
| d04 | digital | Receipt only: no VAT, no line-item table, seller not VAT-registered; the payer's tax ID is not printed |
| d05 | digital | Service invoice with WHT 3 % on a services-only base and net payable; หจก.; Angsana New; tax IDs with spaces |
| d06 | digital | Thai digits for every number; `ก.ย. ๖๙` (2-digit BE year); บมจ. |
| d07 | digital | Two pages: the table continues and the totals are on page 2 only |
| d08 | digital | VAT rounding edge: 7 % of 1,234.50 = 86.415, printed truncated as 86.41. Within tolerance, not a failure |
| d09 | digital | Invoice (ใบแจ้งหนี้) that is not a tax invoice. The document type drives the rules |
| e01 | etax_pdf | e-Tax Invoice PDF with the ETDA XML embedded (ขมธอ. 3-2560 shape). The XML is the source |
| e02 | etax_xml | e-Receipt/Tax Invoice (TypeCode T03) delivered as the XML alone |
| f01 | digital, **fails** | Line 2 prints 1,530.00 for 3 × 450.00 |
| f02 | digital, **fails** | VAT 750.00 on a 10,000.00 base |
| f03 | digital, **fails** | d01 sent again as a copy (สำเนา): duplicate candidate when d01 is in its history; the copy check fails |
| f04 | digital, **fails** | Full tax invoice made out to another company's tax ID |
| f05 | digital, **fails** | Full tax invoice with no seller tax ID |
| f06 | digital, **fails** | Seller tax ID with a wrong check digit |
| f07 | digital, **fails** | USD invoice from a foreign supplier. P.P.36 or section 70 WHT may also be raised |
| s01 / s02 | scan300 / scan200 | d01 scanned at 300 and 200 DPI (image-only PDF) |
| s03 | scan300 | d05 (WHT, Angsana) scanned at 300 DPI |
| s04 | scan200 | d02 (classic) scanned at 200 DPI |
| p01 | photo | Crumpled thermal receipt, tilted about 4°, uneven light. **Triage only** |
| p02 | photo | Flat, well-lit receipt photo. **Triage only** |

Each fixture has a golden file, `expected/<id>.json`. Its fields:
- **`truth`:** the printed values, normalised. Tax IDs are 13 digits, dates are ISO CE, money is a 2-decimal string, rates are percent strings.
- **`checks`:** the status each deterministic check must reach (`pass`, `fail` or `skip`).
- **`history`:** the documents already seen when this one is judged. The duplicate check looks only there; it is empty unless named.
- **`questions`:** `must` lists the questions that have to be asked; `allowed_extra` lists codes that may also be raised; `max` caps how many are allowed; `may` is set for scans and photos.
- **`rules`:** one or both of the two rules below.

The two rules every engine run is held to:
- `no_silent_errors`: each field in scope is either the true value or the subject of a question. A wrong number that nobody asks about is the failure.
- `zero_questions`: nothing is asked. This applies to the clean documents.

What the checks mean:
- `wht`: the printed WHT figures are consistent (base × rate = amount; total − WHT = net).
- `buyer_match`: applies only to full tax invoices, the document input VAT is claimed on.
- `original_copy`: fails for a document marked สำเนา.

`org.json` holds the installing organisation, which is the buyer that `buyer_match` compares against.

## templates/: the Phase E template filler

- **DOCX:**
  - plain placeholders, including one Word split across two runs, and placeholders in the header, footer and a table;
  - a repeating table row (`{{line.*}}`);
  - five content controls: plain text ×2 (to be filled), plus date, rich text and dropdown (to be detected and explained, never silently skipped);
  - a template with no markers, which leads to the mapping screen.
- **XLSX:**
  - named ranges, including one on a formula cell (write refused) and one on a protected sheet (write refused);
  - a table range, whose numbers are written as numbers;
  - formulas whose recalculated values are given;
  - merged cells and a chart;
  - a workbook with no names, which leads to the mapping screen.
- **PPTX:** a theme colour and a chart.

The values are the totals of four E2 invoices (168,000.70, VAT 10,990.70). `expected.json` also says what text a PDF made from each file must contain; this is the `pdftotext` check.

## expense/: Agent 4

- **Policy:**
  - `policy.json` is the structured policy an install stores.
  - `policy.xlsx` is the same policy as a sheet an owner would upload.
  - `holidays.json` is the holiday calendar.
  - `policy_kb.docx` is a policy document with vaguer wording that contradicts the meals limit. It is context only and never the source of a limit.
- **Claims:** 18 claims in `claims_2569-10.xlsx`, with 13 receipts. All merchants are fictional, and every VAT-registered seller has its own valid tax ID.
- **`expected.json`:** each claim's facts and its outcome: `pass`, `exception` (a person decides) or `question` (a judgement card). No outcome is ever an automatic rejection.
- **Period limits** count claimed amounts, because the audit runs before anyone decides an exception.

## Review record (Phase A gate)

1. **`test_am2_fixtures.py`, 10 tests.**
   - Its first run caught two defects: a golden buyer tax ID the receipt layout never prints, and a test that was too naive.
2. **An independent reviewer** compared all 37 page images with the golden files.
   - It found no misread printed value.
   - It found 7 defects, all fixed:
     - the ETDA TypeCode (T01 should be T03);
     - quantities written in exponent form (`2E+1`);
     - the duplicate history left implicit;
     - d02's stated purpose;
     - e01's seller name, which should come from its XML;
     - receipts sharing one tax ID and using real brand names;
     - the page number drawn over the title.
   - It raised these judgement points, now settled:
     - the WHT base on a line for materials: both lines are now services;
     - the copy mark: the `original_copy` check was added;
     - the foreign-payment extras on f07: allowed as extra questions;
     - C02's category: the description now says team dinner;
     - C11's payee ID and address: both are now on the receipt;
     - C16's period rule: claimed amounts count.
