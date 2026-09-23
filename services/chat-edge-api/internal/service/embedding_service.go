// EmbeddingService — turns text into 1536-d embedding vectors.
//
// FIX 2: routes through the upstream Gateway's /v1/embeddings endpoint
// instead of calling OpenAI directly. The direct call bypassed DLP —
// raw KB document content and RAG queries egressed to OpenAI unmasked.
// The gateway now fronts embeddings behind the raw-egress gate
// (credential-block + per-tenant PII policy + audit), so this service
// no longer holds an OpenAI key; it holds a *GatewayClient and forwards
// the end-user identity (tenant + user) so the gateway attributes and
// audits the egress.
//
// If no gateway is wired the service returns ErrEmbeddingDisabled so the
// indexing pipeline surfaces a clear "configure OpenAI on the gateway to
// enable RAG" message instead of indexing zero vectors silently.
package service

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"
)

// EmbeddingDimensions is the column size for chat.knowledge_chunks.
// Pinned to the text-embedding-3-small default (the gateway uses the same
// model); if the gateway ever swaps to a different-dimension model we'll
// need a column-resize migration.
const EmbeddingDimensions = 1536

// ErrEmbeddingDisabled is returned when no gateway is configured to serve
// embeddings. The indexer surfaces this as the document's error_message.
var ErrEmbeddingDisabled = errors.New("embedding: gateway not configured")

// EmbeddingService forwards embedding requests to the gateway.
type EmbeddingService struct {
	gateway *GatewayClient
	// observe (FIX130 C2) is told every call's outcome, so the knowledge
	// availability verdict follows what really happened. Nil = nobody asks.
	observe func(error)
}

// SetObserver wires the FIX130 availability tracker. Call before serving.
func (s *EmbeddingService) SetObserver(fn func(error)) { s.observe = fn }

// Probe is one embedding of a short fixed string on behalf of the person
// asking — the FIX130 availability check when nothing has been observed.
func (s *EmbeddingService) Probe(ctx context.Context, tenantID, userID uuid.UUID) error {
	if !s.Enabled() {
		return ErrEmbeddingDisabled
	}
	_, err := s.gateway.GenerateEmbedding(ctx, embeddingCallCtx(tenantID, userID), "knowledge base availability check")
	return err
}

// NewEmbeddingService wires the gateway client used for every embedding
// call. Passing nil disables the service (Enabled() == false).
func NewEmbeddingService(gateway *GatewayClient) *EmbeddingService {
	return &EmbeddingService{gateway: gateway}
}

// Enabled reports whether embeddings can be produced. In the FIX 2 model
// this means "a gateway is wired" — actual provider readiness (the
// gateway's OPENAI_API_KEY) is the gateway's concern and surfaces as a
// clear per-document failure if unset, mirroring the old behaviour.
func (s *EmbeddingService) Enabled() bool {
	return s.gateway != nil
}

// GenerateEmbedding returns the 1536-d embedding for one text via the
// gateway, forwarding the end-user identity in callCtx so the gateway
// can attribute + audit the (intentional, DLP-gated) raw egress. Empty
// input is rejected locally to save a round-trip on a programming bug.
func (s *EmbeddingService) GenerateEmbedding(
	ctx context.Context, callCtx GatewayCallContext, text string,
) ([]float32, error) {
	if !s.Enabled() {
		if s.observe != nil {
			s.observe(ErrEmbeddingDisabled)
		}
		return nil, ErrEmbeddingDisabled
	}
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, errors.New("embedding: empty input")
	}
	vec, err := s.gateway.GenerateEmbedding(ctx, callCtx, text)
	if s.observe != nil {
		s.observe(err)
	}
	return vec, err
}

// embeddingCallCtx builds the identity envelope for an embedding egress.
// A real end-user (KB upload, RAG query) is attributed directly; system
// paths that have no end-user (reindex, admin, background jobs) fall back
// to the TENANT id as the on-behalf-of marker so the gateway's
// fail-closed service-JWT auth still has a non-empty, tenant-scoped
// X-User-ID. Embeddings are not billed, so this attribution is
// audit-only.
func embeddingCallCtx(tenantID, userID uuid.UUID) GatewayCallContext {
	if userID == uuid.Nil {
		userID = tenantID
	}
	return GatewayCallContext{TenantID: tenantID, UserID: userID}
}
