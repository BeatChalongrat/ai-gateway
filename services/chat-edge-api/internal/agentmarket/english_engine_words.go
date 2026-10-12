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
	"again", "against", "agree", "alike", "all", "allowed", "already", "also", "although", "am10's", "am12", "among",
	"amount", "amounts", "an", "and", "another", "anyway", "apart", "are", "area", "as", "ask", "asked", "asks",
	"asserted", "assumptions", "at", "back", "baht", "bank", "base", "be", "been", "before", "begin", "being",
	"below", "beside", "bill", "blank", "blank's", "blanks", "body", "br", "branch", "budget", "but", "buyer",
	"buyer's", "by", "can", "cannot", "carry", "cell", "centre", "characters", "chart", "charts", "check", "checked",
	"checks", "choose", "cites", "claimed", "claiming", "clear", "clearer", "clearly", "close", "code", "column",
	"columns", "come", "commentary", "companion", "company's", "complete", "computed", "confirm", "confirmed",
	"copy", "correct", "cost", "could", "count", "cover", "credit", "currency", "cut", "data", "date", "dated",
	"dates", "day", "days", "debit", "debit/credit", "decide", "default", "defaults", "description", "detail",
	"details", "did", "differ", "difference", "different", "differs", "digit", "digits", "discount", "do", "doc",
	"docstring", "document", "document's", "documents", "does", "downward", "dpi", "drew", "due", "duplicate",
	"e-tax", "each", "editing", "embedded", "empty", "english", "enough", "enter", "entered", "equal", "estimate",
	"estimated", "every", "evidence", "exact", "example", "excel", "exception", "exchange", "face", "failed", "far",
	"few", "fewer", "field", "fields", "figure", "figures", "file", "files", "fill", "filled", "finished", "first",
	"fit_height", "fit_width", "fits", "fitted", "fixed", "font", "fonts", "footers", "for", "foreign", "form",
	"form's", "format", "formula", "formulas", "found", "freeze", "from", "frozen", "full", "further", "future",
	"gate", "gate's", "give", "given", "gives", "grand", "had", "has", "have", "head", "header", "headers",
	"heading", "height", "hold", "holds", "id", "identifying", "identity", "if", "image", "in", "in-house", "inches",
	"include", "income", "incomplete", "indexes", "input", "instead", "into", "invoice", "is", "it", "item",
	"item's", "items", "its", "itself", "just", "keep", "kept", "key", "kind", "label", "largest", "last", "lead",
	"least", "leave", "left", "like", "line", "lines", "list", "listed", "locked", "long", "longer", "look", "looks",
	"made", "main", "many", "mapped", "marked", "marker", "match", "matching", "may", "me", "merged", "misprinted",
	"misread", "missing", "model", "module", "money", "more", "most", "move", "must", "name", "named", "names",
	"needs", "neither", "net", "no", "nobody", "none", "not", "note", "now", "number", "numbers", "of", "off",
	"office", "old", "on", "once", "one", "only", "opened", "opens", "or", "ordered", "organisation", "original", "other", "out",
	"outside", "outstanding", "overwritten", "own", "package", "page", "paged", "pages", "pair", "pairs", "panes",
	"paper", "papers", "part", "partner", "pass", "past", "payable", "pdf", "per", "period", "person", "photo",
	"pick", "picture", "pictures", "plain-text", "plan", "please", "possible", "price", "print", "printed", "prints",
	"prior", "proposed", "quantity", "range", "rate", "reaches", "read", "readable", "reading", "reads", "really",
	"receipt", "record", "recorded", "redraw", "reference", "references", "register", "rendered", "reopened",
	"repeat", "repeat_cols", "repeated", "report", "required", "retail", "returns", "reviewer", "right", "round",
	"round's", "row", "row's", "rows", "run", "run's", "runs", "s", "same", "satang", "say", "saying", "scaled",
	"scan", "section", "sections", "see", "seller", "seller's", "send", "sent", "set", "setup", "several", "shape",
	"sheet", "sheet's", "sheets", "shop", "short", "should", "show", "shows", "side", "size", "skip", "skipped",
	"slide", "slides", "small", "smallest", "so", "some", "sound", "source", "speaks", "statement", "stay", "stays",
	"still", "structure", "styles", "such", "sum", "summary", "sure", "suspect", "system", "table", "table's",
	"tables", "taken", "tax", "text", "thai", "than", "that", "the", "their", "them", "theme", "theme's", "there",
	"these", "they", "this", "those", "time", "tin", "to", "too", "total", "totalling", "totals", "try", "two",
	"type", "typed", "unclear", "under", "unfilled", "unit", "unusually", "up", "upload", "use", "used", "user",
	"uses", "value", "values", "vat", "very", "was", "ways", "well", "were", "what", "when", "whether", "which",
	"whole", "whose", "wht", "wide", "wider", "width", "will", "with", "withheld", "withholding", "within",
	"without", "words", "workbook", "working", "writers", "wrong", "x", "xlsx.print", "xlsx.print.one_across", "xml",
	"y", "yet", "you", "your", "zero",
}
