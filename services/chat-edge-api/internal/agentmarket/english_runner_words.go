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
	"agreeing", "ai", "all", "already", "also", "although", "am", "amount", "an", "and", "answer", "answered",
	"answering", "answers", "any", "anyone", "applied", "are", "area", "as", "ask", "asked", "asking", "assistant",
	"at", "author", "back", "base", "be", "because", "been", "before", "began", "below", "blank", "box", "built",
	"but", "buttons", "by", "calculate", "calculated", "calculating", "calculation", "can", "candidate",
	"candidates", "cannot", "card", "cause", "certainly", "characters", "chat", "check", "checked", "checking",
	"checks", "choice", "choose", "chosen", "cited", "clear", "comes", "commentary", "compare", "compared",
	"completeness", "confidence", "confirmed", "content", "conversion", "converted", "converter", "converting",
	"copy", "corrected", "could", "counts", "credits", "criteria", "current", "d", "damaged", "data", "days",
	"debits", "decide", "decision", "decode", "default", "defaults", "delivered", "depends", "details", "did",
	"differ", "differs", "displayed", "do", "document", "documents", "docx", "does", "done", "draft", "drafted",
	"drag", "each", "earlier", "embedded", "enough", "entries", "equal", "error", "every", "evidence", "exactly",
	"example", "excel", "excluded", "expected", "expects", "external", "extra", "failed", "far", "fields", "figure",
	"figures", "file", "file's", "files", "filled", "finished", "finishes", "first", "fix", "font", "fonts", "for",
	"form", "format", "formulas", "found", "from", "frozen", "gave", "give", "got", "guess", "has", "have", "header",
	"here", "history", "hold", "holds", "how", "hr", "i", "if", "in", "ink", "inside", "instead", "into", "is", "it",
	"item", "items", "its", "itself", "kept", "know", "knowledge", "known", "last", "later", "libreoffice", "like",
	"link", "look", "made", "make", "making", "margins", "marker", "matched", "may", "measure", "meets", "message",
	"metadata", "missing", "model", "model's", "models", "moment", "more", "most", "name", "names", "need", "needs",
	"new", "no", "none", "not", "noted", "nothing", "now", "ocr", "of", "off", "ok", "older", "on", "one", "ones",
	"only", "open", "opened", "opening", "or", "organisation", "organisation's", "original", "other", "out",
	"outside", "page", "page's", "pages", "paper", "paragraphs", "parts", "pass", "passed", "password", "past",
	"paste", "pdf", "period", "photographed", "pick", "powerpoint", "pptx", "prepared", "presentation", "press",
	"previous", "print", "printed", "prints", "problem", "product", "program", "protected", "provider", "quality",
	"question", "questions", "raise", "re-read", "read", "reading", "ready", "reason", "reasons", "recalculate",
	"recalculated", "recalculation", "received", "rechecked", "record", "relevant", "reopened", "replace",
	"replaces", "report", "request", "result", "results", "review", "reviewer's", "roll", "row", "rows", "rules",
	"run", "run's", "runs", "s", "same", "sample", "save", "saved", "saving", "saw", "says", "scanned", "script",
	"scripts", "search", "searches", "searching", "see", "self-test.json", "seller", "sellers", "send", "sent",
	"set", "settings", "sha-256", "sheet", "should", "show", "slides", "small", "so", "some", "sorry", "source",
	"sources", "specific", "start", "starting", "starts", "stay", "steps", "still", "stopped", "succeed", "summary",
	"sure", "switched", "switching", "system", "system's", "table", "tables", "tap", "tell", "test", "test's",
	"text", "thai", "than", "that", "thb", "the", "their", "them", "theme", "then", "there", "things", "this",
	"through", "times", "to", "totals", "towards", "try", "type", "typed", "unusual", "up", "upgrade", "upgraded",
	"use", "used", "value", "version", "waited", "waiting", "want", "was", "web", "well", "were", "what", "when",
	"which", "why", "will", "with", "without", "word", "work", "workbook", "worked", "working", "would", "writing",
	"written", "xls", "xlsx", "yet", "you", "your",
}
