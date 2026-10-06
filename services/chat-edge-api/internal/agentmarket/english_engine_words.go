package agentmarket

// ── The words of the engine's English sentences (AM12) ───────────────────
//
// The Python engine writes what its own checks of a working paper found
// (backend/agent_market/deliverables/workpaper.py: "prints one page wide",
// "the header row repeated on every page" …), in the language of the run.
// This plane reads a file's checks back before a person is sent them
// (schema.go), and in English shows only words the product's own sentences
// use (reads.go). The engine's sentences are Python, which the server does
// not link — so their words are told here. The list is read from that file,
// not thought up: TestTheEnginesEnglishWordsAreKnownToTheControlPlane fails
// on a word of its English this list lacks and on a word here that it no
// longer uses, and writes the list out (AM12_WORDS=<file>).
//
// A package's own sentences are in its manifest and are learnt from there
// (english_package.go); nothing of a package is in this list.

func init() { AddEnglishWords(engineEnglishWords...) }

var engineEnglishWords = []string{
	"1-based", "a", "a4", "across", "am10's", "am12", "amount", "amounts", "an", "and", "are", "area", "as", "asserted",
	"at", "back", "be", "begin", "below", "but", "carry", "characters", "check", "checked", "checks", "column",
	"columns", "companion", "complete", "count", "cover", "default", "description", "differs", "do", "docstring",
	"document's", "does", "downward", "each", "english", "every", "evidence", "excel", "face", "fewer", "file",
	"finished", "first", "fit_height", "fit_width", "fitted", "font", "for", "format", "freeze", "from", "frozen",
	"gate", "gate's", "has", "header", "heading", "hold", "holds", "identifying", "identity", "in", "inches", "indexes",
	"is", "it", "item", "its", "last", "lead", "left", "line", "long", "main", "may", "module", "money", "more", "move",
	"must", "name", "named", "needs", "no", "not", "number", "numbers", "of", "on", "one", "only", "package", "page",
	"paged", "pages", "pairs", "panes", "paper", "papers", "pick", "plan", "print", "printed", "prints", "reaches",
	"read", "readable", "reopened", "repeat", "repeat_cols", "repeated", "returns", "row", "row's", "run", "same",
	"scaled", "see", "set", "setup", "several", "shape", "sheet", "sheet's", "sheets", "should", "small", "so",
	"speaks", "stay", "summary", "table's", "than", "that", "the", "their", "this", "those", "to", "too", "under",
	"used", "what", "whole", "wide", "wider", "width", "with", "workbook", "working", "writers", "x", "xlsx.print",
	"xlsx.print.one_across", "y", "zero",
}
