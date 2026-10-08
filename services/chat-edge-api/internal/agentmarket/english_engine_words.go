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
// AM14: the shared engine's other sentences — a matching card, what a file
// read back showed — are kept by key in engine/words.json, Thai and English;
// the words of their English are in this list by the same test.
//
// A package's own sentences are in its manifest and are learnt from there
// (english_package.go); nothing of a package is in this list.

func init() { AddEnglishWords(engineEnglishWords...) }

var engineEnglishWords = []string{
	"1-based", "a", "a4", "about", "across", "add", "agree", "alike", "all", "allowed", "also", "am10's", "am12",
	"among", "amount", "amounts", "an", "and", "another", "apart", "are", "area", "as", "ask", "asked", "asserted",
	"at", "back", "baht", "be", "begin", "below", "but", "by", "can", "carry", "characters", "check", "checked",
	"checks", "close", "column", "columns", "companion", "complete", "computed", "confirm", "confirmed", "correct",
	"could", "count", "cover", "date", "dated", "dates", "day", "days", "decide", "default", "description", "differ",
	"difference", "different", "differs", "do", "docstring", "document's", "documents", "does", "downward", "each",
	"empty", "english", "equal", "every", "evidence", "exact", "excel", "face", "fewer", "file", "files", "finished",
	"first", "fit_height", "fit_width", "fitted", "font", "for", "format", "found", "freeze", "from", "frozen",
	"further", "gate", "gate's", "has", "have", "header", "heading", "hold", "holds", "identifying", "identity",
	"in", "inches", "indexes", "is", "it", "item", "items", "its", "itself", "keep", "last", "lead", "least", "left",
	"line", "list", "long", "main", "match", "matching", "may", "me", "module", "money", "more", "most", "move",
	"must", "name", "named", "needs", "no", "not", "number", "numbers", "of", "on", "once", "one", "only", "or",
	"outstanding", "own", "package", "page", "paged", "pages", "pair", "pairs", "panes", "paper", "papers",
	"partner", "person", "pick", "plan", "please", "possible", "print", "printed", "prints", "proposed", "range",
	"reaches", "read", "readable", "record", "reference", "references", "reopened", "repeat", "repeat_cols",
	"repeated", "returns", "row", "row's", "run", "run's", "same", "satang", "scaled", "see", "send", "set", "setup",
	"several", "shape", "sheet", "sheet's", "sheets", "should", "show", "side", "small", "so", "speaks", "stay",
	"stays", "such", "summary", "sure", "suspect", "table", "table's", "than", "that", "the", "their", "them",
	"there", "they", "this", "those", "to", "too", "totalling", "totals", "under", "up", "used", "values", "what",
	"when", "whole", "whose", "wide", "wider", "width", "with", "within", "workbook", "working", "writers", "x",
	"xlsx.print", "xlsx.print.one_across", "y", "yet", "you", "zero",
}
