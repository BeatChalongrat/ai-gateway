// EmbeddingService — turns text into 1536-d embedding vectors.
//
// Calls OpenAI's ``/v1/embeddings`` directly with the
// ``text-embedding-3-small`` model (1536 dims to match the
// pgvector(1536) column). Direct rather than via the upstream
// Gateway because:
//
//   - the Gateway doesn't currently surface an embeddings route,
//   - keeping the embedding call point-to-point removes a hop from
//     the indexing critical path,
//   - the embeddings model is dedicated + cheap so a separate budget
//     ceiling isn't pulling its weight yet.
//
// If OPENAI_API_KEY is unset the service returns ErrEmbeddingDisabled
// so the indexing pipeline can surface a clear "configure OpenAI to
// enable RAG" message instead of indexing zero vectors silently.
package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// EmbeddingDimensions is the column size for chat.knowledge_chunks.
// Pinned to the text-embedding-3-small default; if we ever swap to a
// model with a different default we'll need to either pass ``dimensions``
// in the OpenAI request or run a column-resize migration.
const EmbeddingDimensions = 1536

// ErrEmbeddingDisabled is returned when no provider key is configured.
// The indexer surfaces this as the document's ``error_message``.
var ErrEmbeddingDisabled = errors.New("embedding: provider not configured (OPENAI_API_KEY)")

// EmbeddingService wraps the upstream embeddings endpoint.
type EmbeddingService struct {
	apiKey  string
	baseURL string
	model   string
	http    *http.Client
}

// NewEmbeddingService reads OPENAI_API_KEY at construction time so a
// later env mutation doesn't change behaviour mid-process. The base
// URL is also env-overridable (OPENAI_BASE_URL) so customers running
// against Azure OpenAI or a self-hosted compatible endpoint can swap
// it without a rebuild.
func NewEmbeddingService() *EmbeddingService {
	base := os.Getenv("OPENAI_BASE_URL")
	if base == "" {
		base = "https://api.openai.com/v1"
	}
	model := os.Getenv("OPENAI_EMBEDDING_MODEL")
	if model == "" {
		model = "text-embedding-3-small"
	}
	return &EmbeddingService{
		apiKey:  os.Getenv("OPENAI_API_KEY"),
		baseURL: strings.TrimRight(base, "/"),
		model:   model,
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

// Enabled reports whether the service has the credentials needed to
// produce real embeddings. Used by the KB UI / handler to fail
// upload fast with a "configure OpenAI" message instead of failing
// inside the indexer.
func (s *EmbeddingService) Enabled() bool {
	return s.apiKey != ""
}

// GenerateEmbedding returns the 1536-d embedding for one text. Empty
// input is rejected — the OpenAI endpoint also rejects it but a
// localrequest saves the round-trip on a programming bug.
func (s *EmbeddingService) GenerateEmbedding(ctx context.Context, text string) ([]float32, error) {
	if !s.Enabled() {
		return nil, ErrEmbeddingDisabled
	}
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, errors.New("embedding: empty input")
	}
	body, err := json.Marshal(map[string]any{
		"model": s.model,
		"input": text,
	})
	if err != nil {
		return nil, fmt.Errorf("embedding marshal: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		s.baseURL+"/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("embedding request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+s.apiKey)
	resp, err := s.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("embedding http: %w", err)
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embedding api %d: %s", resp.StatusCode, truncateEmb(string(respBody), 200))
	}
	var parsed struct {
		Data []struct {
			Embedding []float32 `json:"embedding"`
		} `json:"data"`
	}
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		return nil, fmt.Errorf("embedding decode: %w", err)
	}
	if len(parsed.Data) == 0 || len(parsed.Data[0].Embedding) == 0 {
		return nil, errors.New("embedding api returned empty data")
	}
	return parsed.Data[0].Embedding, nil
}

func truncateEmb(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
