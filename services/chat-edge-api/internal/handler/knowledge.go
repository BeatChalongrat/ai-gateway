// KnowledgeHandler — HTTP surface for the RAG / Knowledge Base
// feature.
//
// Routes (all behind RequireAuth):
//
//	POST   /api/v1/knowledge-bases                       create KB
//	GET    /api/v1/knowledge-bases                       list KBs
//	GET    /api/v1/knowledge-bases/{id}                  get KB
//	PATCH  /api/v1/knowledge-bases/{id}                  update KB
//	DELETE /api/v1/knowledge-bases/{id}                  delete KB (cascade)
//	GET    /api/v1/knowledge-bases/{id}/documents        list docs
//	POST   /api/v1/knowledge-bases/{id}/documents        upload doc (202; indexed in the background)
//	DELETE /api/v1/knowledge-bases/{id}/documents/{docId} delete doc
//	POST   /api/v1/knowledge-bases/{id}/documents/{docId}/reindex
//	POST   /api/v1/knowledge-bases/{id}/search           debug RAG search
//
// All tenant scoping happens inside KnowledgeService — the handlers
// only do request parsing + status mapping, mirroring file.go.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type KnowledgeHandler struct {
	svc *service.KnowledgeService
	rag *service.RAGService
	// avail (FIX130 C2) answers GET /knowledge-bases/availability; nil =
	// no kb-models wired, which is "models_unavailable".
	avail *service.KnowledgeAvailability
	// maxMemMultipartMB — same value as file.go for parity.
	maxMemMultipartMB int64
}

func NewKnowledgeHandler(svc *service.KnowledgeService, rag *service.RAGService) *KnowledgeHandler {
	return &KnowledgeHandler{svc: svc, rag: rag, maxMemMultipartMB: 64}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type kbResponse struct {
	ID             string `json:"id"`
	Name           string `json:"name"`
	Description    string `json:"description"`
	EmbeddingModel string `json:"embedding_model"`
	ChunkSize      int    `json:"chunk_size"`
	ChunkOverlap   int    `json:"chunk_overlap"`
	IsActive       bool   `json:"is_active"`
	DocumentCount  int    `json:"document_count"`
	TotalChunks    int    `json:"total_chunks"`
	CreatedAt      string `json:"created_at"`
	UpdatedAt      string `json:"updated_at"`
}

func toKBResponse(kb *model.KnowledgeBase) kbResponse {
	return kbResponse{
		ID:             kb.ID.String(),
		Name:           kb.Name,
		Description:    kb.Description,
		EmbeddingModel: kb.EmbeddingModel,
		ChunkSize:      kb.ChunkSize,
		ChunkOverlap:   kb.ChunkOverlap,
		IsActive:       kb.IsActive,
		DocumentCount:  kb.DocumentCount,
		TotalChunks:    kb.TotalChunks,
		CreatedAt:      kb.CreatedAt.UTC().Format(time.RFC3339),
		UpdatedAt:      kb.UpdatedAt.UTC().Format(time.RFC3339),
	}
}

type kbDocResponse struct {
	ID              string  `json:"id"`
	KnowledgeBaseID string  `json:"knowledge_base_id"`
	Filename        string  `json:"filename"`
	ContentType     string  `json:"content_type"`
	FileSizeBytes   int64   `json:"file_size_bytes"`
	Status          string  `json:"status"`
	ChunkCount      int     `json:"chunk_count"`
	CharCount       int     `json:"char_count"`
	ErrorMessage    *string `json:"error_message,omitempty"`
	CreatedAt       string  `json:"created_at"`
	UpdatedAt       string  `json:"updated_at"`
}

func toKBDocResponse(d *model.KnowledgeDocument) kbDocResponse {
	return kbDocResponse{
		ID:              d.ID.String(),
		KnowledgeBaseID: d.KnowledgeBaseID.String(),
		Filename:        d.OriginalFilename,
		ContentType:     d.ContentType,
		FileSizeBytes:   d.FileSizeBytes,
		Status:          d.Status,
		ChunkCount:      d.ChunkCount,
		CharCount:       d.CharCount,
		ErrorMessage:    d.ErrorMessage,
		CreatedAt:       d.CreatedAt.UTC().Format(time.RFC3339),
		UpdatedAt:       d.UpdatedAt.UTC().Format(time.RFC3339),
	}
}

// ---------------------------------------------------------------------------
// KB CRUD
// ---------------------------------------------------------------------------

type createKBRequest struct {
	Name         string `json:"name"`
	Description  string `json:"description"`
	ChunkSize    int    `json:"chunk_size"`
	ChunkOverlap int    `json:"chunk_overlap"`
}

func (h *KnowledgeHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	var req createKBRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	kb, err := h.svc.CreateKB(r.Context(), service.CreateKBInput{
		TenantID:     caller.TenantUUID,
		UserID:       caller.UserID,
		Name:         req.Name,
		Description:  req.Description,
		ChunkSize:    req.ChunkSize,
		ChunkOverlap: req.ChunkOverlap,
	})
	if err != nil {
		if errors.Is(err, service.ErrKBNameRequired) {
			WriteError(w, http.StatusBadRequest, CodeValidationError, "name is required")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(toKBResponse(kb))
}

// SetAvailability wires the FIX130 C2 availability tracker.
func (h *KnowledgeHandler) SetAvailability(a *service.KnowledgeAvailability) { h.avail = a }

// Availability — GET /api/v1/knowledge-bases/availability (FIX130 C2).
// Whether a knowledge base can work right now: {available, reason,
// checked_at}. The clients show no knowledge-base affordance unless this
// says available, and the knowledge page says why when it does not.
func (h *KnowledgeHandler) Availability(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	v := service.KBAvailability{Available: false, Reason: service.KBUnavailableModels, CheckedAt: time.Now().UTC()}
	if h.avail != nil {
		v = h.avail.Status(r.Context(), caller.TenantUUID, caller.UserID)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(v)
}

func (h *KnowledgeHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbs, err := h.svc.ListKBs(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	out := make([]kbResponse, 0, len(kbs))
	for i := range kbs {
		out = append(out, toKBResponse(&kbs[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"knowledge_bases": out})
}

func (h *KnowledgeHandler) Get(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	kb, err := h.svc.GetKB(r.Context(), caller.TenantUUID, caller.UserID, kbID)
	if err != nil {
		if errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	docs, err := h.svc.ListDocuments(r.Context(), caller.TenantUUID, caller.UserID, kbID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	docOut := make([]kbDocResponse, 0, len(docs))
	for i := range docs {
		docOut = append(docOut, toKBDocResponse(&docs[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"knowledge_base": toKBResponse(kb),
		"documents":      docOut,
	})
}

type updateKBRequest struct {
	Name        *string `json:"name,omitempty"`
	Description *string `json:"description,omitempty"`
	IsActive    *bool   `json:"is_active,omitempty"`
}

func (h *KnowledgeHandler) Update(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	var req updateKBRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Name == nil && req.Description == nil && req.IsActive == nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "no fields to update")
		return
	}
	if err := h.svc.UpdateKB(r.Context(), caller.TenantUUID, caller.UserID, kbID, service.UpdateKBInput{
		Name:        req.Name,
		Description: req.Description,
		IsActive:    req.IsActive,
	}); err != nil {
		if errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	kb, err := h.svc.GetKB(r.Context(), caller.TenantUUID, caller.UserID, kbID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toKBResponse(kb))
}

func (h *KnowledgeHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	if err := h.svc.DeleteKB(r.Context(), caller.TenantUUID, caller.UserID, kbID); err != nil {
		if errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

func (h *KnowledgeHandler) ListDocuments(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	docs, err := h.svc.ListDocuments(r.Context(), caller.TenantUUID, caller.UserID, kbID)
	if err != nil {
		if errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	out := make([]kbDocResponse, 0, len(docs))
	for i := range docs {
		out = append(out, toKBDocResponse(&docs[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"documents": out})
}

func (h *KnowledgeHandler) UploadDocument(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	if err := r.ParseMultipartForm(h.maxMemMultipartMB * 1024 * 1024); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid multipart body")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "missing 'file' field")
		return
	}
	defer file.Close()

	ct := header.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/octet-stream"
	}
	doc, err := h.svc.UploadDocument(r.Context(), service.UploadDocumentInput{
		TenantID:        caller.TenantUUID,
		UserID:          caller.UserID,
		KnowledgeBaseID: kbID,
		OriginalName:    header.Filename,
		ContentType:     ct,
		Size:            header.Size,
		Body:            file,
	})
	if err != nil {
		h.writeUploadError(w, err)
		return
	}
	// FIX132 B — indexing runs in the background: 202 with the pending row;
	// the client follows status / progress_done / progress_total.
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(toKBDocResponse(doc))
}

func (h *KnowledgeHandler) DeleteDocument(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	docID, err := uuid.Parse(chi.URLParam(r, "docId"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid document id")
		return
	}
	if err := h.svc.DeleteDocument(r.Context(), caller.TenantUUID, caller.UserID, kbID, docID); err != nil {
		if errors.Is(err, service.ErrKBDocumentNotFound) || errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "document not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (h *KnowledgeHandler) ReindexDocument(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	docID, err := uuid.Parse(chi.URLParam(r, "docId"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid document id")
		return
	}
	doc, err := h.svc.ReindexDocument(r.Context(), caller.TenantUUID, caller.UserID, kbID, docID)
	if err != nil {
		if errors.Is(err, service.ErrKBDocumentNotFound) || errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "document not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(toKBDocResponse(doc))
}

func (h *KnowledgeHandler) writeUploadError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrKBNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
	case errors.Is(err, service.ErrKBDocumentEmpty):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "empty upload")
	case errors.Is(err, service.ErrKBDocumentTooLarge):
		WriteError(w, http.StatusRequestEntityTooLarge, CodeValidationError, "document too large")
	case errors.Is(err, service.ErrKBDocumentBadType):
		WriteError(w, http.StatusUnsupportedMediaType, CodeValidationError, "document type not allowed (PDF, DOCX, XLSX, CSV, TXT)")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// Debug RAG search
// ---------------------------------------------------------------------------

type searchKBRequest struct {
	Query string `json:"query"`
	// Mode — dense | sparse | hybrid | hybrid_rerank (FIX132 B); empty =
	// what chat uses.
	Mode string `json:"mode"`
	TopK int    `json:"top_k"`
	// MinScore overrides KB_MIN_SCORE for this search (eval); omitted = the
	// configured floor.
	MinScore *float64 `json:"min_score,omitempty"`
}

type searchKBHit struct {
	ChunkID     string   `json:"chunk_id"`
	DocumentID  string   `json:"document_id"`
	Filename    string   `json:"filename"`
	ChunkIndex  int      `json:"chunk_index"`
	Content     string   `json:"content"`
	Score       float64  `json:"score"`
	RerankScore *float64 `json:"rerank_score,omitempty"`
	Cosine      float64  `json:"cosine"`
	Lexical     float64  `json:"lexical"`
}

func (h *KnowledgeHandler) Search(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	var req searchKBRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if strings.TrimSpace(req.Query) == "" {
		WriteError(w, http.StatusBadRequest, CodeValidationError, "query is required")
		return
	}
	mode, ok := service.ParseSearchMode(req.Mode)
	if !ok {
		WriteError(w, http.StatusBadRequest, CodeValidationError, "mode must be dense, sparse, hybrid or hybrid_rerank")
		return
	}
	// Sanity check the KB exists for this tenant before running
	// retrieval — otherwise the search would silently return nothing.
	if _, err := h.svc.GetKB(r.Context(), caller.TenantUUID, caller.UserID, kbID); err != nil {
		if errors.Is(err, service.ErrKBNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	res, err := h.rag.SearchWith(r.Context(), caller.TenantUUID, kbID, req.Query, mode, req.TopK, service.SearchOptions{MinScore: req.MinScore})
	if err != nil {
		if service.IsKBModelsUnavailable(err) {
			WriteError(w, http.StatusServiceUnavailable, "KB_MODELS_UNAVAILABLE",
				"the knowledge base models are not available right now")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	out := make([]searchKBHit, 0, len(res.Chunks))
	for _, c := range res.Chunks {
		out = append(out, searchKBHit{
			ChunkID:     c.ChunkID.String(),
			DocumentID:  c.DocumentID.String(),
			Filename:    c.Filename,
			ChunkIndex:  c.ChunkIndex,
			Content:     c.Content,
			Score:       c.Score,
			RerankScore: c.Rerank,
			Cosine:      c.Cosine,
			Lexical:     c.Lexical,
		})
	}
	body := map[string]any{"results": out, "mode": res.Mode, "timing_ms": res.Timing, "below_floor": res.BelowFloor}
	if res.RerankFallback != "" {
		body["rerank_fallback"] = res.RerankFallback
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(body)
}
