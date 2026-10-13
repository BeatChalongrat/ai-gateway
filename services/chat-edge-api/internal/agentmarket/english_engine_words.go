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
	"1-based", "5-digit", "a", "a4", "abbreviated", "about", "account", "accounting", "across", "add", "added", "addr",
	"address", "adjusting", "administrator", "after", "again", "against", "agent", "agent's", "agree", "alike", "all",
	"allowed", "already", "also", "although", "am10's", "am12", "among", "amount", "amounts", "an", "analysis", "and",
	"another", "answer", "answered", "anyway", "apart", "appendix", "approved", "approves", "are", "area", "as", "ask",
	"asked", "asking", "asks", "asserted", "assumptions", "at", "automatically", "b", "back", "baht", "balance", "bank",
	"base", "be", "because", "been", "before", "begin", "being", "below", "beside", "bill", "blank", "blank's",
	"blanks", "body", "br", "branch", "budget", "but", "buyer", "buyer's", "by", "c", "c12", "can", "cannot", "carry",
	"cash", "cell", "centre", "change", "changed", "characters", "charcoal", "chart", "charts", "check", "checked",
	"checks", "choose", "cites", "claimed", "claiming", "clear", "clearer", "clearly", "close", "closing", "code",
	"column", "columns", "come", "comes", "commentary", "companion", "company's", "compared", "comparison", "complete",
	"composition", "computation", "computed", "confirm", "confirmed", "converted", "copy", "corporate", "correct",
	"cost", "could", "count", "counted", "cover", "credit", "currency", "cut", "data", "date", "dated", "dates", "day",
	"days", "debit", "debit/credit", "decide", "decides", "decimal", "decimals", "default", "defaults", "delivered",
	"description", "detail", "details", "did", "differ", "difference", "different", "differs", "digit", "digits",
	"discount", "do", "doc", "docstring", "document", "document's", "documents", "docx", "does", "downward", "dpi",
	"drawn", "draws", "drew", "due", "duplicate", "e", "e-tax", "e.g", "each", "earlier", "editing", "email",
	"embedded", "empty", "english", "enough", "enter", "entered", "entries", "entry", "equal", "estimate", "estimated",
	"every", "evidence", "exact", "example", "excel", "exception", "exceptions", "exchange", "executive", "expenses",
	"face", "failed", "far", "few", "fewer", "field", "fields", "figure", "figure's", "figures", "file", "files",
	"fill", "filled", "finished", "first", "fit_height", "fit_width", "fits", "fitted", "fixed", "flagged", "flow",
	"font", "fonts", "footers", "for", "foreign", "form", "form's", "format", "formula", "formulas", "found", "freeze",
	"from", "frozen", "full", "further", "future", "gate", "gate's", "general", "give", "given", "gives", "grand",
	"group", "had", "has", "have", "head", "header", "headers", "heading", "height", "hold", "holds", "id",
	"identifying", "identity", "if", "image", "in", "in-house", "inches", "include", "income", "incomplete", "increase",
	"indexes", "input", "inside", "instead", "into", "invoice", "is", "it", "item", "item's", "items", "its", "itself",
	"just", "keep", "kept", "key", "kind", "l", "label", "large", "largest", "last", "lead", "least", "leave", "ledger",
	"left", "like", "line", "lines", "list", "listed", "locked", "long", "longer", "look", "looks", "m", "made", "mail",
	"main", "management", "many", "mapped", "margin", "marked", "marker", "match", "matching", "materiality", "may",
	"me", "meeting", "merged", "millions", "misprinted", "misread", "missing", "model", "module", "money", "month",
	"month-on-month", "more", "most", "move", "movements", "moves", "must", "n", "name", "named", "names", "navy",
	"needs", "negative", "neither", "net", "new", "next", "no", "nobody", "none", "not", "note", "nothing", "now",
	"number", "numbers", "observations", "of", "off", "office", "old", "on", "once", "one", "only", "opened", "opens",
	"or", "ordered", "organisation", "original", "other", "out", "outside", "outstanding", "over", "overwritten", "own",
	"p", "package", "page", "paged", "pages", "pair", "pairs", "panes", "paper", "papers", "part", "partner", "pass",
	"past", "payable", "pdf", "per", "performance", "period", "period's", "person", "photo", "pick", "picture",
	"pictures", "placed", "plain-text", "plan", "please", "point", "point's", "positive", "possible", "pp", "prepared",
	"preparer", "price", "primary", "print", "printed", "prints", "prior", "prior-period", "profit", "proposed",
	"provider", "quantity", "range", "rate", "reaches", "read", "read-only", "readable", "reading", "reads", "really",
	"reason", "receipt", "recommended", "recomputed", "recomputes", "record", "recorded", "redraw", "ref", "reference",
	"references", "refers", "register", "remembered", "rendered", "reopened", "repeat", "repeat_cols", "repeated",
	"report", "required", "responsible", "retail", "returns", "revenue", "review", "review&rpage", "reviewed",
	"reviewer", "right", "role", "round", "round's", "row", "row's", "rows", "rule", "rules", "run", "run's", "runs",
	"s", "sales", "same", "satang", "say", "saying", "says", "scaled", "scan", "scope", "script", "section", "sections",
	"see", "sees", "seller", "seller's", "send", "sends", "sent", "server", "services", "set", "setup", "several",
	"sha256", "shape", "sheet", "sheet's", "sheets", "shop", "short", "should", "show", "shown", "shows", "side",
	"single", "size", "skip", "skipped", "slide", "slides", "small", "smallest", "so", "some", "sound", "source",
	"sources", "speaks", "stamped", "statement", "stay", "stays", "steps", "still", "structure", "styles", "such",
	"sum", "sumifs", "summary", "sure", "suspect", "system", "system's", "table", "table's", "tables", "taken", "tax",
	"teal", "text", "thai", "than", "that", "thb", "the", "their", "them", "theme", "theme's", "there", "these", "they",
	"this", "those", "threshold", "through", "time", "tin", "to", "too", "total", "totalling", "totals", "trend",
	"trends", "trial", "try", "twice", "two", "type", "typed", "types", "unbalanced", "unclear", "under", "unfilled",
	"unit", "unusual", "unusually", "up", "upload", "use", "used", "user", "uses", "v", "value", "values", "variance",
	"vat", "verification", "verify", "very", "vs", "was", "ways", "well", "were", "what", "when", "whether", "which",
	"whole", "whose", "wht", "wide", "wider", "width", "will", "with", "withheld", "withholding", "within", "without",
	"words", "workbook", "working", "write", "writers", "writes", "written", "wrong", "wrote", "x", "xlsx.print",
	"xlsx.print.one_across", "xml", "y", "yet", "you", "your", "zero",
}
