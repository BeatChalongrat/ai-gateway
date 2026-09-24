package service

// FIX131 A1 — the file a plan names is INPUT unless it is the same kind as
// the file the plan makes.
//
// NIGHT3: a PDF attached, the model proposed a Word plan whose
// source_file_id was the PDF, and "สร้างเอกสาร" answered 502 in ~3 s with
// "doc: generator failed: script reported failure". ExecutePlan handed every
// docx/pptx source to the generator's EDIT path, which copies the source to
// the output and opens it as a Word/PowerPoint file — a PDF is neither. The
// Excel executor did the same with a CSV/PDF/DOCX source (load_workbook).
//
// ChatGPT and Claude read an attached file and write a NEW one; they edit an
// upload only when it is the same kind of document. Now:
//
//	edit  — the source is the same kind as the output (.docx → .docx,
//	        .pptx → .pptx, .xlsx → .xlsx): the generator edits a copy (the
//	        upload itself is never touched).
//	input — anything else (PDF, image, text, CSV, another Office kind): the
//	        model already read it (its text was in the prompt) and the plan
//	        carries the content; the generator builds a new file without
//	        opening it. The output still records where it came from
//	        (parent_file_id → the input), so the card can say "from X".
//	none  — no source: create from scratch.
//
// A PDF is always built new (pdf_generator.py takes no source), so every
// source of a PDF plan is input — DOCX → PDF works exactly as before.

import (
	"path/filepath"
	"strings"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// PlanSourceRole is what a plan's source file is for.
type PlanSourceRole string

const (
	PlanSourceNone  PlanSourceRole = "none"
	PlanSourceEdit  PlanSourceRole = "edit"
	PlanSourceInput PlanSourceRole = "input"
)

// OfficeKind names a file's document kind: docx | pptx | xlsx | pdf, or
// "other". The content type decides; the extension is consulted only when
// the upload carried no useful type (empty / application/octet-stream).
func OfficeKind(contentType, filename string) string {
	ct := strings.ToLower(strings.TrimSpace(contentType))
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = strings.TrimSpace(ct[:i])
	}
	switch {
	case IsDocxContentType(ct):
		return "docx"
	case IsPptxContentType(ct):
		return "pptx"
	case IsExcelContentType(ct):
		return "xlsx"
	case IsPdfContentType(ct):
		return "pdf"
	}
	if ct == "" || ct == "application/octet-stream" {
		switch strings.ToLower(filepath.Ext(strings.TrimSpace(filename))) {
		case ".docx":
			return "docx"
		case ".pptx":
			return "pptx"
		case ".xlsx":
			return "xlsx"
		case ".pdf":
			return "pdf"
		}
	}
	return "other"
}

// planOutputKind normalises an execute call's format ("excel" is the Excel
// route's own name for xlsx).
func planOutputKind(format string) string {
	switch f := strings.ToLower(strings.TrimSpace(format)); f {
	case "excel":
		return "xlsx"
	default:
		return f
	}
}

// PlanSourceRoleFor decides what the plan's source is for, given the format
// the plan makes. Pure: the rule is testable without a sandbox or database.
func PlanSourceRoleFor(format string, src *model.ConversationFile) PlanSourceRole {
	if src == nil {
		return PlanSourceNone
	}
	out := planOutputKind(format)
	if out == "pdf" {
		return PlanSourceInput
	}
	switch out {
	case "docx", "pptx", "xlsx":
		if OfficeKind(src.ContentType, src.OriginalFilename) == out {
			return PlanSourceEdit
		}
	}
	return PlanSourceInput
}
