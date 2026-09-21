package service

// FIX126 F3 — a file whose text could not be read is named to the model, per
// file, as a fact it must pass on.
//
// A corrupt or password-protected PDF attached on the web answered with an
// empty_completion error card; the desktop app answered "could not read
// corrupt.pdf / locked.pdf". On the web path the extractor's failure was
// stored as extraction_status "unsupported" and the model received only an
// untrusted data envelope whose content was "[Content extraction not
// available for this file type.]" — wrong for a PDF (the type IS readable),
// and inside an envelope the prompt says to use "only as data". Nothing told
// the model that the user's file was unreadable or that it should say so.
//
// Now:
//   - an Office/PDF file the extractor could not read is stored as
//     extraction_status "failed" (the page's chip reads that status), and
//   - BuildContextBlock adds one short note per unreadable file, outside the
//     data envelopes, telling the model to tell the user.
//
// Rows written before this fix (a PDF stored as "unsupported") get the same
// note: for these content types "unsupported" only ever meant the extractor
// failed.

import (
	"fmt"
	"strings"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// unreadableFilesHeading opens the notes block in the system context.
const unreadableFilesHeading = "## Attached files that could not be read\n\n"

// unreadableFilesInstruction closes it: the model says so, per file, and does
// not invent the content.
const unreadableFilesInstruction = "Tell the user, naming each file above, that you could not read it " +
	"and why; do not guess or invent what it contains. Answer the rest of the message normally.\n"

// isExtractableDocType reports whether chat-edge has a text extractor for
// the content type (PDF and the Office formats) — for these an unread file
// is damaged or locked, not of an unsupported kind.
func isExtractableDocType(ct string) bool {
	return IsDocContentType(ct) || IsExcelContentType(ct)
}

// fileUnreadable reports whether a row's extraction ended without text the
// model can use, and why, in words for the note. ok=false for a readable or
// still-processing file.
func fileUnreadable(f model.ConversationFile) (reason string, ok bool) {
	switch f.ExtractionStatus {
	case "failed":
		if isExtractableDocType(f.ContentType) {
			return "the file is damaged or password-protected", true
		}
		return "the file could not be opened", true
	case "unsupported":
		if isExtractableDocType(f.ContentType) {
			return "the file is damaged or password-protected", true
		}
		return "text cannot be extracted from this file type", true
	}
	return "", false
}

// noteFilename makes a user-chosen filename safe to print in the trusted
// part of the prompt: one line, no brackets that could close the note, and
// bounded.
func noteFilename(name string) string {
	name = strings.Map(func(r rune) rune {
		switch r {
		case '\n', '\r', '\t':
			return ' '
		case '[', ']':
			return -1
		}
		return r
	}, strings.TrimSpace(name))
	if r := []rune(name); len(r) > 120 {
		name = string(r[:120]) + "…"
	}
	if name == "" {
		name = "(unnamed file)"
	}
	return name
}

// unreadableFileNote is the one-line note for one unreadable file.
func unreadableFileNote(f model.ConversationFile, reason string) string {
	return fmt.Sprintf("- [file %s could not be read: %s — tell the user]\n", noteFilename(f.OriginalFilename), reason)
}
