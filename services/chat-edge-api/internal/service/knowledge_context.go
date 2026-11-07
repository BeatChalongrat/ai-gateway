package service

// FIX132 B — the knowledge-base block in the system prompt, and the
// context_sources it leaves on the answer.
//
// The block (prompt compiler layer 13) holds the retrieved passages,
// numbered, each labelled [Source: <filename>], with the rules the vendors
// give their own file search: cite the file you took a fact from, and say
// plainly when the passages do not answer the question. When some of the
// knowledge base's files are still being indexed, it says so, so the model
// answers from the ready documents and tells the user the rest are coming.
//
// Every passage given to the model is recorded as a `knowledge_chunk`
// source (source_id = chunk id; metadata: kb_id, document_id, filename,
// chunk_index, excerpt ≤ 500 chars, score, rank), and the not-yet-indexed
// files as one `knowledge_pending` source (metadata: kb_id, pending,
// names ≤ 5). They are persisted with the answer before message_complete and
// come back on GET /messages like the other source types.
//
// Budget: the block stops before KB_CONTEXT_MAX_CHARS characters and before
// a quarter of the model's context window (EstimateTokens, the estimator the
// FIX102–108 context budget uses), so a 16k-window model gets a handful of
// passages and a 128k one up to KB_TOP_K.

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// Source types for the knowledge base (response_context_sources.source_type).
const (
	SourceTypeKnowledgeChunk   = model.SourceTypeKnowledgeChunk
	SourceTypeKnowledgePending = model.SourceTypeKnowledgePending
)

const (
	kbExcerptRunes      = 500
	kbPendingNamesShown = 5
)

// RAGRequest is one turn's knowledge-base retrieval.
type RAGRequest struct {
	TenantID uuid.UUID
	// UserID is the asker: only the knowledge base's owner may read it.
	UserID uuid.UUID
	KBID   uuid.UUID
	Query  string
	// MaxTokens caps the block (a quarter of the model's context window);
	// 0 = only KB_CONTEXT_MAX_CHARS.
	MaxTokens int
}

// KnowledgeBlockInput is what the block is built from.
type KnowledgeBlockInput struct {
	KBName       string
	Chunks       []RetrievedChunk
	Pending      int
	PendingNames []string
	// Unavailable: the knowledge base could not be searched (kb-models down).
	Unavailable bool
	MaxChars    int
	MaxTokens   int
}

// BuildContext is the RAGReader entry point: search the knowledge base for
// this turn and render the block and its sources. Never fails the turn: a
// problem yields a smaller block (or none), logged.
func (s *RAGService) BuildContext(ctx context.Context, req RAGRequest) (string, []ContextSource) {
	if s == nil || s.repo == nil {
		return "", nil
	}
	kb, err := s.repo.GetKB(ctx, req.TenantID, req.KBID)
	if err != nil {
		return "", nil
	}
	if kb.UserID == nil || *kb.UserID != req.UserID {
		// Private to its owner (FIX132 B): a binding that points at someone
		// else's knowledge base reads nothing.
		slog.Warn("kb context: knowledge base is not the asker's", "kb_id", req.KBID.String())
		return "", nil
	}
	in := KnowledgeBlockInput{KBName: kb.Name, MaxChars: s.cfg.ContextMaxChars, MaxTokens: req.MaxTokens}
	in.Pending, in.PendingNames, _ = s.repo.NotReadyDocuments(ctx, kb.ID, kbPendingNamesShown)
	if kb.TotalChunks > 0 && strings.TrimSpace(req.Query) != "" {
		res, err := s.Search(ctx, req.TenantID, kb.ID, req.Query, "", 0)
		switch {
		case err == nil:
			in.Chunks = res.Chunks
			slog.Info("kb context", "kb_id", kb.ID.String(), "mode", res.Mode, "passages", len(res.Chunks),
				"below_floor", res.BelowFloor, "pending", in.Pending, "embed_ms", res.Timing.Embed,
				"sql_ms", res.Timing.SQL, "rerank_ms", res.Timing.Rerank, "rerank_fallback", res.RerankFallback)
		case ctx.Err() != nil:
			return "", nil
		default:
			in.Unavailable = IsKBModelsUnavailable(err)
			slog.Warn("kb context: search failed; answering without it", "kb_id", kb.ID.String(), "err", err)
		}
	}
	block, used := BuildKnowledgeBlock(in)
	return block, knowledgeSources(kb.ID, kb.Name, used, in.Pending, in.PendingNames)
}

// BuildKnowledgeBlock renders the block and returns the passages it holds
// (after the budget). "" when there is nothing to say: no passage passed,
// nothing is pending and the search did not fail — then the model answers
// as if no knowledge base were attached.
func BuildKnowledgeBlock(in KnowledgeBlockInput) (string, []RetrievedChunk) {
	if len(in.Chunks) == 0 && in.Pending == 0 && !in.Unavailable {
		return "", nil
	}
	name := oneLine(in.KBName)
	const closing = "</knowledge_base>"
	head := kbBlockHead(name, true, in)
	var body strings.Builder
	used := make([]RetrievedChunk, 0, len(in.Chunks))
	for i, c := range in.Chunks {
		passage := fmt.Sprintf("\n[%d] [Source: %s]\n%s\n", len(used)+1, citeName(c.Filename), fenceSafe(strings.TrimSpace(c.Content)))
		next := head + body.String() + passage + closing
		if in.MaxChars > 0 && utf8.RuneCountInString(next) > in.MaxChars {
			break
		}
		if in.MaxTokens > 0 && EstimateTokens(next) > in.MaxTokens {
			break
		}
		body.WriteString(passage)
		used = append(used, in.Chunks[i])
	}
	if len(used) == 0 {
		if in.Pending == 0 && !in.Unavailable {
			return "", nil // nothing passed (or not even one passage fits the budget)
		}
		head = kbBlockHead(name, false, in)
	}
	return head + body.String() + closing, used
}

// kbBlockHead is the block's opening tag and instructions.
func kbBlockHead(name string, passages bool, in KnowledgeBlockInput) string {
	var b strings.Builder
	// The heading marks everything below as DATA for the gateway's
	// system-prompt echo guard (prompt_echo_guard.py _DATA_HEADING_RE): a
	// passage quoted word for word in the answer is a grounded answer, not
	// an echo of instructions.
	b.WriteString("## Retrieved knowledge base passages\n")
	fmt.Fprintf(&b, "<knowledge_base name=%q>\n", name)
	switch {
	case passages:
		fmt.Fprintf(&b, "Passages retrieved from the user's knowledge base %q for this message, most relevant first.\n", name)
		b.WriteString("- Use them to answer when they are relevant. After each fact you take from a passage, cite it as [Source: <filename>] with the filename exactly as shown in the passage's label.\n")
		b.WriteString("- If the passages do not contain the answer, say plainly that the knowledge base does not contain it. Do not present general knowledge as coming from the knowledge base.\n")
	case in.Unavailable:
		fmt.Fprintf(&b, "The user's knowledge base %q could not be searched right now. If the question depends on it, say so plainly instead of guessing.\n", name)
	default:
		fmt.Fprintf(&b, "No passage from the user's knowledge base %q matched this message.\n", name)
	}
	if in.Pending > 0 {
		files := "files are"
		if in.Pending == 1 {
			files = "file is"
		}
		fmt.Fprintf(&b, "- %d %s still being processed and cannot be searched yet", in.Pending, files)
		if len(in.PendingNames) > 0 {
			names := make([]string, len(in.PendingNames))
			for i, n := range in.PendingNames {
				names[i] = citeName(n)
			}
			fmt.Fprintf(&b, " (%s", strings.Join(names, ", "))
			if in.Pending > len(in.PendingNames) {
				fmt.Fprintf(&b, ", and %d more", in.Pending-len(in.PendingNames))
			}
			b.WriteString(")")
		}
		b.WriteString(". Answer from the ready documents, and if the answer might be in those files, tell the user they are still being processed.\n")
	}
	return b.String()
}

// knowledgeSources records what the model was given.
func knowledgeSources(kbID uuid.UUID, kbName string, used []RetrievedChunk, pending int, names []string) []ContextSource {
	out := make([]ContextSource, 0, len(used)+1)
	for i, c := range used {
		score := c.Score
		if c.Rerank != nil {
			score = *c.Rerank
		}
		out = append(out, ContextSource{
			SourceType:   SourceTypeKnowledgeChunk,
			SourceID:     c.ChunkID.String(),
			DisplayLabel: TruncateLabel(c.Filename),
			Metadata: MarshalMeta(map[string]any{
				"kb_id":       kbID.String(),
				"document_id": c.DocumentID.String(),
				"filename":    c.Filename,
				"chunk_index": c.ChunkIndex,
				"excerpt":     excerpt(c.Content, kbExcerptRunes),
				"score":       score,
				"rank":        i + 1,
			}),
		})
	}
	if pending > 0 {
		if names == nil {
			names = []string{}
		}
		out = append(out, ContextSource{
			SourceType:   SourceTypeKnowledgePending,
			SourceID:     kbID.String(),
			DisplayLabel: TruncateLabel(kbName),
			Metadata: MarshalMeta(map[string]any{
				"kb_id":   kbID.String(),
				"pending": pending,
				"names":   names,
			}),
		})
	}
	return out
}

// citeName is the filename as the model should cite it: one line, no
// brackets (they would end the [Source: …] marker early).
func citeName(s string) string {
	s = oneLine(s)
	s = strings.NewReplacer("[", "(", "]", ")").Replace(s)
	if s == "" {
		return "untitled"
	}
	return s
}

func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }

// fenceSafe keeps a passage from closing the block early.
func fenceSafe(s string) string {
	return strings.ReplaceAll(s, "</knowledge_base", "</ knowledge_base")
}

// excerpt is the first n runes, whitespace collapsed.
func excerpt(s string, n int) string {
	s = oneLine(s)
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}
