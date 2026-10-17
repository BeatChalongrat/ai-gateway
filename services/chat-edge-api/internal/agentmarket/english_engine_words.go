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
	"1-based", "5-digit", "6-digit", "a", "a1", "a4", "abbreviated", "about", "account", "accounting", "across",
	"add", "added", "addr", "address", "adjusting", "administrator", "after", "again", "against", "agent", "agent's",
	"agree", "ai", "alike", "all", "allowed", "already", "also", "although", "am12", "am23", "among", "amount",
	"amount's", "amounts", "an", "analysis", "and", "another", "answer", "answered", "anyway", "apart", "appendix",
	"approved", "approves", "are", "area", "as", "ask", "asked", "asking", "asks", "asserted", "assistant",
	"assumptions", "at", "automatically", "available", "b", "b1", "back", "baht", "balance", "bank", "base", "be",
	"been", "before", "begin", "being", "below", "beside", "bill", "blank", "blank's", "blanks", "block", "body",
	"br", "branch", "brand", "budget", "build", "but", "buyer", "buyer's", "by", "c", "c12", "came", "can", "cannot",
	"card", "carry", "cash", "catalogue", "catalogue's", "cell", "centre", "change", "changed", "character",
	"characters", "charcoal", "chart", "charts", "check", "checked", "checks", "choose", "chosen", "cite", "cited",
	"cites", "claimed", "claiming", "claims", "clear", "clearer", "clearly", "close", "closing", "code", "codes",
	"colour", "colours", "column", "columns", "come", "comes", "commentary", "companion", "company's", "compared",
	"comparison", "complete", "composed", "composition", "computation", "computed", "confirm", "confirmed",
	"content", "contrast", "converted", "copy", "corporate", "correct", "cost", "could", "count", "counted", "cover",
	"credit", "currency", "cursor", "cut", "data", "date", "dated", "dates", "day", "days", "debit", "debit/credit",
	"decide", "decides", "decimal", "decimals", "deck", "default", "defaults", "delivered", "description", "detail",
	"details", "did", "differ", "difference", "different", "differs", "digit", "digits", "discount", "do", "doc",
	"docstring", "document", "document's", "documents", "docx", "does", "downward", "dpi", "drafted", "drawn",
	"draws", "drew", "due", "duplicate", "e", "e-tax", "e.g", "each", "earlier", "editing", "email", "embedded",
	"empty", "english", "enough", "enter", "entered", "entries", "entry", "equal", "estimate", "estimated", "even",
	"every", "everything", "everywhere", "evidence", "exact", "example", "excel", "excel's", "exception",
	"exceptions", "exchange", "executive", "expenses", "face", "failed", "far", "few", "fewer", "field", "fields",
	"figure", "figure's", "figures", "file", "file's", "files", "fill", "filled", "financial", "finished", "first",
	"fit", "fit_height", "fit_width", "fits", "fitted", "fixed", "flagged", "flow", "following", "font", "fonts",
	"footer", "footers", "for", "foreign", "form", "form's", "format", "formats", "formatted", "formula", "formulas",
	"found", "frame", "freeze", "from", "frozen", "full", "further", "future", "gate", "general", "give", "given",
	"gives", "go", "grand", "group", "guessed", "had", "hand", "has", "have", "head", "header", "headers", "heading",
	"hedge", "hedges", "height", "hex", "hidden", "hold", "holds", "how", "id", "identifying", "identity", "if",
	"image", "in", "in-house", "inches", "include", "income", "incomplete", "increase", "indexes", "input",
	"instead", "into", "invoice", "invoices", "is", "it", "item", "item's", "items", "its", "itself", "jpeg", "just",
	"keep", "kept", "key", "kind", "l", "label", "lacks", "large", "largest", "last", "layout", "layout's",
	"layouts", "lead", "least", "leave", "ledger", "left", "like", "limit", "line", "lines", "list", "listed",
	"lists", "locked", "logo", "long", "longer", "look", "looks", "m", "made", "mail", "main", "make", "makes",
	"management", "many", "mapped", "margin", "marked", "marker", "match", "matched", "matching", "materiality",
	"may", "me", "meaning", "meeting", "merged", "metadata", "millions", "misprinted", "misread", "missing", "model",
	"model's", "module", "money", "month", "month-on-month", "more", "most", "move", "movements", "moves", "must",
	"n", "name", "named", "names", "narrow", "navy", "needs", "negative", "neither", "net", "never", "new", "next",
	"no", "nobody", "none", "not", "note", "nothing", "now", "number", "numbers", "observations", "of", "off",
	"office", "old", "on", "once", "one", "only", "opened", "opens", "or", "order", "ordered", "organisation",
	"organisation's", "original", "other", "out", "outside", "outstanding", "over", "overwritten", "own", "p",
	"package", "page", "paged", "pages", "pair", "pairs", "palette", "panes", "paper", "paragraph", "paragraphs",
	"part", "partner", "parts", "pass", "passed", "past", "payable", "pdf", "per", "performance", "period",
	"period's", "person", "photo", "pick", "picture", "pictures", "placed", "placeholder", "placeholders",
	"plain-text", "plan", "please", "png", "point", "point's", "points", "positive", "possible", "pp", "prepared",
	"preparer", "price", "primary", "print", "printed", "prints", "prior", "prior-period", "product", "profit",
	"proposed", "pt", "px", "quantity", "range", "rate", "reaches", "read", "read-only", "readable", "reading",
	"reads", "really", "reason", "receipt", "recommended", "recomputed", "recomputes", "reconciling", "record",
	"recorded", "records", "redraw", "ref", "reference", "references", "refers", "refuses", "register", "remembered",
	"rendered", "reopened", "repeat", "repeat_cols", "repeated", "replaced", "report", "report's", "required",
	"responsible", "rest", "result", "retail", "returns", "revenue", "review", "review&rpage", "reviewed",
	"reviewer", "right", "right-hand", "role", "round", "round's", "row", "row's", "rows", "rule", "rules", "run",
	"run's", "runs", "sales", "same", "satang", "say", "saying", "says", "scale", "scaled", "scan", "scope",
	"script", "scripts", "section", "sections", "see", "seller", "seller's", "send", "sends", "sent", "sentence",
	"sentences", "server", "services", "set", "setup", "several", "sha-256", "sha256", "shape", "sheet", "sheet's",
	"sheets", "shop", "short", "should", "show", "shown", "shows", "side", "single", "size", "sizes", "skip",
	"skipped", "slide", "slides", "small", "smaller", "smallest", "so", "some", "sound", "source", "sources",
	"speaks", "stamped", "statement", "stay", "stays", "step", "steps", "still", "structure", "style", "styles",
	"such", "sum", "sumifs", "summary", "sure", "suspect", "system", "system's", "table", "table's", "tables",
	"taken", "tax", "teal", "template", "template's", "text", "texts", "thai", "than", "that", "thb", "the", "their",
	"them", "theme", "theme's", "there", "these", "they", "thing", "things", "this", "those", "though", "threshold",
	"through", "time", "tin", "to", "too", "total", "totalling", "totals", "trend", "trends", "trial", "try",
	"twice", "two", "type", "typed", "typeface", "unbalanced", "unclear", "under", "unfilled", "unit", "unusual",
	"unusually", "up", "upload", "use", "used", "user", "uses", "v", "value", "values", "variance", "vat",
	"verification", "verify", "very", "vs", "was", "ways", "well", "were", "what", "when", "where", "whether",
	"which", "who", "whole", "whose", "wht", "wide", "wider", "width", "will", "with", "withheld", "withholding",
	"within", "without", "word", "words", "workbook", "working", "would", "writers", "writes", "written", "wrong",
	"wrote", "x", "xlsx.print", "xlsx.print.one_across", "xml", "y", "yet", "you", "your", "zero",
}
