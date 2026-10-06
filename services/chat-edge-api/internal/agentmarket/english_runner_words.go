package agentmarket

// ── The words of the runner's English sentences (AM11) ───────────────────
//
// The runner writes a thread item; this plane reads it back before a person
// is sent it (schema.go), and shows only words the product's own sentences
// use (reads.go). The runner's sentences are in the runner's package
// (runner/english.go), which the server does not link — so their words are
// told here. The list is read from those sentences, not thought up:
// TestTheRunnersEnglishWordsAreKnownToTheControlPlane fails on a word of
// theirs this list lacks and on a word here that none of them uses, and
// writes the list out (AM11_WORDS=<file>).

func init() { AddEnglishWords(runnerEnglishWords...) }

var runnerEnglishWords = []string{
	"a", "abnormal", "about", "above", "added", "admin", "advance", "after", "again", "against", "agent", "agent's",
	"agreeing", "all", "already", "also", "although", "am", "amount", "an", "and", "another", "answer", "answered",
	"answering", "answers", "any", "anyone", "applied", "are", "as", "ask", "asked", "asking", "at", "back", "base",
	"be", "because", "been", "before", "began", "below", "blank", "box", "built", "but", "buttons", "by", "calculate",
	"calculated", "calculating", "calculation", "can", "candidate", "candidates", "cannot", "card", "cause", "chat",
	"check", "checked", "checking", "checks", "choice", "choose", "chosen", "clear", "come", "comes", "commentary",
	"compare", "compared", "completeness", "confidence", "confirmed", "conversion", "convert", "converter", "corrected",
	"could", "counts", "credits", "criteria", "current", "d", "data", "days", "debits", "decide", "decision", "default",
	"defaults", "delivered", "details", "did", "differ", "differs", "displayed", "do", "document", "documents", "docx",
	"does", "done", "draft", "drag", "each", "earlier", "english", "enough", "entries", "equal", "error", "every",
	"evidence", "exactly", "example", "excel", "excluded", "expected", "expects", "external", "extra", "far", "fields",
	"figure", "figures", "file", "files", "filled", "finished", "finishes", "first", "fix", "for", "form", "format",
	"formulas", "found", "from", "gave", "give", "got", "guess", "has", "have", "here", "history", "hold", "how", "hr",
	"i", "if", "in", "inside", "instead", "into", "is", "it", "item", "items", "its", "itself", "kept", "kind", "know",
	"knowledge", "known", "last", "later", "libreoffice", "like", "link", "look", "made", "make", "makes", "making",
	"matched", "meets", "message", "missing", "model", "models", "moment", "more", "name", "names", "need", "needs",
	"new", "next", "no", "none", "not", "noted", "nothing", "now", "ocr", "of", "off", "ok", "on", "one", "only",
	"open", "opened", "opening", "or", "organisation", "original", "other", "out", "outside", "page", "pages",
	"paragraphs", "parts", "pass", "passed", "paste", "pdf", "period", "photographed", "powerpoint", "pptx", "prepared",
	"presentation", "press", "previous", "printed", "problem", "program", "provider", "quality", "question",
	"questions", "raise", "re-read", "read", "reading", "ready", "reason", "reasons", "recalculate", "recalculated",
	"recalculation", "received", "rechecked", "record", "relevant", "reopened", "replace", "replaces", "report",
	"request", "result", "results", "review", "reviewer's", "roll", "row", "rows", "rules", "run", "run's", "runs", "s",
	"same", "sample", "save", "saved", "saving", "saw", "says", "scanned", "script", "scripts", "search", "searches",
	"searching", "see", "self-test.json", "seller", "sellers", "send", "sent", "set", "settings", "sheet", "should",
	"show", "slides", "small", "so", "some", "sorry", "source", "sources", "specific", "start", "starting", "starts",
	"stay", "steps", "still", "stopped", "succeed", "summary", "sure", "switched", "switching", "system", "system's",
	"table", "tables", "tell", "test", "test's", "text", "thai", "than", "that", "thb", "the", "their", "them", "theme",
	"then", "there", "these", "things", "this", "times", "to", "totals", "towards", "try", "type", "typed", "unusual",
	"up", "upgrade", "upgraded", "use", "value", "version", "waited", "waiting", "want", "was", "web", "well", "were",
	"what", "when", "which", "why", "will", "with", "without", "word", "work", "workbook", "worked", "working", "would",
	"writing", "written", "xlsx", "yet", "you", "your",
}
