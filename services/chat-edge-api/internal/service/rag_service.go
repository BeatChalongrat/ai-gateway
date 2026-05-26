// RAGService — query-time retrieval for the knowledge base.
//
// The message flow calls QueryKnowledgeBase with the user's most
// recent prompt right before constructing the gateway request. The
// returned chunks become a markdown context block in the system
// prompt with [Source: filename] citation hints, so the model can
// answer from KB material instead of hallucinating.
//
// Similarity threshold (0.70) was picked to balance recall vs.
// noise — empirically, chunks under 0.70 cosine to a focused query
// tend to be off-topic and degrade the system prompt's signal-to-
// noise ratio. Configurable via env if a tenant needs to tune it.
package service

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

const (
	defaultRAGTopK      = 5
	defaultRAGThreshold = 0.70
)

// RetrievedChunk is the wire-friendly view of a single retrieval
// hit. Used both by the debug-search endpoint AND by the message
// service when assembling the context block.
type RetrievedChunk struct {
	Content    string
	Score      float64
	DocumentID uuid.UUID
	Filename   string
	ChunkIndex int
}

type RAGService struct {
	repo     *repository.KnowledgeRepo
	embedder *EmbeddingService
	topK     int
	thresh   float64
}

func NewRAGService(repo *repository.KnowledgeRepo, embedder *EmbeddingService) *RAGService {
	topK := defaultRAGTopK
	if v := os.Getenv("RAG_TOP_K"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			topK = n
		}
	}
	thresh := defaultRAGThreshold
	if v := os.Getenv("RAG_SIMILARITY_THRESHOLD"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			thresh = f
		}
	}
	return &RAGService{
		repo:     repo,
		embedder: embedder,
		topK:     topK,
		thresh:   thresh,
	}
}

// Enabled reports whether the RAG path can run. False when the
// embedding provider isn't configured — the message service uses
// this to short-circuit the retrieval branch.
func (s *RAGService) Enabled() bool { return s.embedder.Enabled() }

// QueryKnowledgeBase embeds ``query`` and returns the top matching
// chunks from the named KB. topK == 0 → service default.
func (s *RAGService) QueryKnowledgeBase(
	ctx context.Context, tenantID, kbID uuid.UUID, query string, topK int,
) ([]RetrievedChunk, error) {
	if !s.Enabled() {
		return nil, ErrEmbeddingDisabled
	}
	if topK <= 0 {
		topK = s.topK
	}
	emb, err := s.embedder.GenerateEmbedding(ctx, query)
	if err != nil {
		return nil, err
	}
	rows, err := s.repo.SearchSimilarChunks(ctx, tenantID, kbID, emb, topK, s.thresh)
	if err != nil {
		return nil, err
	}
	out := make([]RetrievedChunk, 0, len(rows))
	for _, r := range rows {
		out = append(out, RetrievedChunk{
			Content:    r.Chunk.Content,
			Score:      r.Similarity,
			DocumentID: r.Chunk.DocumentID,
			Filename:   r.DocumentFilename,
			ChunkIndex: r.Chunk.ChunkIndex,
		})
	}
	return out, nil
}

// BuildContext is the convenience entry point the message service
// uses to satisfy the RAGReader interface — embed the query, search
// the KB, format the result. Errors are swallowed (returns "") so a
// retrieval glitch never blocks the reply.
func (s *RAGService) BuildContext(
	ctx context.Context, tenantID, kbID uuid.UUID, query string,
) string {
	chunks, err := s.QueryKnowledgeBase(ctx, tenantID, kbID, query, 0)
	if err != nil {
		return ""
	}
	return BuildRAGContext(chunks)
}

// BuildRAGContext formats retrieved chunks into a markdown block
// suitable for prepending to a system prompt. Citation format is
// fixed at ``[Source: filename]`` so the message bubble's parser
// can pick it up reliably.
//
// Empty input → empty string so the message service can safely
// concat the result regardless of whether retrieval found anything.
func BuildRAGContext(chunks []RetrievedChunk) string {
	if len(chunks) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString("## Retrieved Knowledge Base Context\n\n")
	b.WriteString("Use the following information to answer the user's question. ")
	b.WriteString("Cite your sources using [Source: filename] format. ")
	b.WriteString("If the knowledge base doesn't contain relevant information ")
	b.WriteString("for the user's question, say so explicitly rather than guessing.\n\n")
	for i, c := range chunks {
		b.WriteString("---\n")
		fmt.Fprintf(&b, "[Source: %s, section %d]\n", c.Filename, c.ChunkIndex+1)
		b.WriteString(strings.TrimSpace(c.Content))
		b.WriteString("\n")
		if i == len(chunks)-1 {
			b.WriteString("---\n\n")
		}
	}
	return b.String()
}
