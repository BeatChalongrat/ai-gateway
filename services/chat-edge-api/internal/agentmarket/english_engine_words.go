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
	"1-based", "5-digit", "a", "a4", "abbreviated", "about", "account", "across", "add", "addr", "address", "after",
	"again", "agree", "alike", "all", "allowed", "already", "also", "although", "am10's", "am12", "among", "amount",
	"amounts", "an", "and", "another", "anyway", "apart", "are", "area", "as", "ask", "asked", "asks", "asserted",
	"at", "back", "baht", "bank", "base", "be", "been", "before", "begin", "being", "below", "bill", "br", "branch",
	"but", "buyer", "buyer's", "by", "can", "cannot", "carry", "characters", "check", "checked", "checks", "choose",
	"claimed", "claiming", "clear", "clearer", "clearly", "close", "column", "columns", "companion", "company's",
	"complete", "computed", "confirm", "confirmed", "copy", "correct", "could", "count", "cover", "credit",
	"currency", "data", "date", "dated", "dates", "day", "days", "debit", "decide", "default", "description",
	"details", "differ", "difference", "different", "differs", "digit", "digits", "discount", "do", "doc",
	"docstring", "document", "document's", "documents", "does", "downward", "dpi", "due", "duplicate", "e-tax",
	"each", "empty", "english", "enough", "enter", "entered", "equal", "every", "evidence", "exact", "example",
	"excel", "exception", "exchange", "face", "failed", "far", "fewer", "figures", "file", "files", "finished",
	"first", "fit_height", "fit_width", "fitted", "font", "for", "foreign", "format", "found", "freeze", "from",
	"frozen", "full", "further", "future", "gate", "gate's", "give", "given", "grand", "had", "has", "have", "head",
	"header", "heading", "hold", "holds", "id", "identifying", "identity", "if", "image", "in", "in-house", "inches",
	"include", "incomplete", "indexes", "input", "instead", "invoice", "is", "it", "item", "item's", "items", "its",
	"itself", "just", "keep", "kind", "last", "lead", "least", "leave", "left", "like", "line", "lines", "list",
	"long", "look", "looks", "made", "main", "marked", "match", "matching", "may", "me", "misprinted", "misread",
	"missing", "model", "module", "money", "more", "most", "move", "must", "name", "named", "needs", "neither",
	"net", "no", "nobody", "none", "not", "note", "now", "number", "numbers", "of", "office", "old", "on", "once",
	"one", "only", "or", "original", "other", "out", "outstanding", "own", "package", "page", "paged", "pages",
	"pair", "pairs", "panes", "paper", "papers", "partner", "pass", "payable", "pdf", "per", "person", "photo",
	"pick", "picture", "plan", "please", "possible", "price", "print", "printed", "prints", "proposed", "quantity",
	"range", "rate", "reaches", "read", "readable", "reading", "reads", "really", "receipt", "record", "recorded",
	"reference", "references", "register", "reopened", "repeat", "repeat_cols", "repeated", "retail", "returns",
	"reviewer", "right", "row", "row's", "run", "run's", "same", "satang", "say", "saying", "scaled", "scan", "see",
	"seller", "seller's", "send", "sent", "set", "setup", "several", "shape", "sheet", "sheet's", "sheets", "shop",
	"should", "show", "shows", "side", "skip", "skipped", "small", "so", "some", "speaks", "stay", "stays", "such",
	"sum", "summary", "sure", "suspect", "system", "table", "table's", "taken", "tax", "text", "than", "that", "the",
	"their", "them", "there", "these", "they", "this", "those", "time", "tin", "to", "too", "total", "totalling",
	"totals", "try", "two", "type", "typed", "unclear", "under", "unit", "unusually", "up", "upload", "use", "used",
	"user", "value", "values", "vat", "was", "ways", "well", "were", "what", "when", "whether", "which", "whole",
	"whose", "wht", "wide", "wider", "width", "with", "withheld", "withholding", "within", "without", "words",
	"workbook", "working", "writers", "wrong", "x", "xlsx.print", "xlsx.print.one_across", "xml", "y", "yet", "you",
	"your", "zero",
}
