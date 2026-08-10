// Canvas-artifact handlers — versioned inline artifacts per conversation
// (Phase 6/C3, migration 030).
//
// All routes sit behind RequireAuth (see router.go); every handler pulls
// the caller from the request context and the service asserts
// conversation ownership before touching a row. Same shape as the
// project handlers.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type CanvasArtifactHandler struct {
	artifacts *service.CanvasArtifactService
}

func NewCanvasArtifactHandler(a *service.CanvasArtifactService) *CanvasArtifactHandler {
	return &CanvasArtifactHandler{artifacts: a}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type canvasArtifactResponse struct {
	ID             string    `json:"id"`
	ConversationID string    `json:"conversation_id"`
	MessageID      *string   `json:"message_id"`
	ArtifactKey    string    `json:"artifact_key"`
	Type           string    `json:"type"`
	Title          *string   `json:"title"`
	Content        string    `json:"content"`
	Version        int       `json:"version"`
	CreatedAt      time.Time `json:"created_at"`
}

func toCanvasArtifactResponse(a *model.CanvasArtifact) canvasArtifactResponse {
	r := canvasArtifactResponse{
		ID:             a.ID.String(),
		ConversationID: a.ConversationID.String(),
		ArtifactKey:    a.ArtifactKey,
		Type:           a.Type,
		Title:          a.Title,
		Content:        a.Content,
		Version:        a.Version,
		CreatedAt:      a.CreatedAt,
	}
	if a.MessageID != nil {
		s := a.MessageID.String()
		r.MessageID = &s
	}
	return r
}

type saveCanvasArtifactRequest struct {
	ArtifactKey string `json:"artifact_key"`
	Type        string `json:"type"`
	Title       string `json:"title"`
	Content     string `json:"content"`
	// MessageID optionally links the assistant message that produced
	// this version; omitted / empty means unlinked.
	MessageID string `json:"message_id"`
}

type revertCanvasArtifactRequest struct {
	Version int `json:"version"`
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// callerAndConv resolves the auth caller + the {id} conversation path
// param shared by every route in this file.
func (h *CanvasArtifactHandler) callerAndConv(
	w http.ResponseWriter, r *http.Request,
) (middleware.Caller, uuid.UUID, bool) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return middleware.Caller{}, uuid.Nil, false
	}
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return middleware.Caller{}, uuid.Nil, false
	}
	return caller, convID, true
}

// artifactKeyParam decodes the {key} path segment. Keys are client-chosen
// strings, so the segment arrives percent-encoded — unescape and reject
// empty.
func artifactKeyParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	key, err := url.PathUnescape(chi.URLParam(r, "key"))
	if err != nil || key == "" {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid artifact key")
		return "", false
	}
	return key, true
}

// List — GET /conversations/{id}/artifacts. The newest version of every
// artifact in the conversation, newest first.
func (h *CanvasArtifactHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, convID, ok := h.callerAndConv(w, r)
	if !ok {
		return
	}
	artifacts, err := h.artifacts.ListLatest(r.Context(), caller.TenantUUID, caller.UserID, convID)
	if err != nil {
		h.writeArtifactError(w, err)
		return
	}
	items := make([]canvasArtifactResponse, 0, len(artifacts))
	for i := range artifacts {
		items = append(items, toCanvasArtifactResponse(&artifacts[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"artifacts": items})
}

// Save — POST /conversations/{id}/artifacts
// { "artifact_key": "...", "type": "html", "title": "x.html",
//
//	"content": "...", "message_id": "<uuid>"? }.
//
// Always appends the NEXT version for the key (v1 when the key is new).
func (h *CanvasArtifactHandler) Save(w http.ResponseWriter, r *http.Request) {
	caller, convID, ok := h.callerAndConv(w, r)
	if !ok {
		return
	}
	var req saveCanvasArtifactRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	var messageID *uuid.UUID
	if req.MessageID != "" {
		id, err := uuid.Parse(req.MessageID)
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message_id")
			return
		}
		messageID = &id
	}
	a, err := h.artifacts.Save(
		r.Context(), caller.TenantUUID, caller.UserID, convID,
		messageID, req.ArtifactKey, req.Type, req.Title, req.Content,
	)
	if err != nil {
		h.writeArtifactError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]any{"artifact": toCanvasArtifactResponse(a)})
}

// Versions — GET /conversations/{id}/artifacts/{key}/versions. Every
// version for one artifact, version DESC.
func (h *CanvasArtifactHandler) Versions(w http.ResponseWriter, r *http.Request) {
	caller, convID, ok := h.callerAndConv(w, r)
	if !ok {
		return
	}
	key, ok := artifactKeyParam(w, r)
	if !ok {
		return
	}
	versions, err := h.artifacts.ListVersions(r.Context(), caller.TenantUUID, caller.UserID, convID, key)
	if err != nil {
		h.writeArtifactError(w, err)
		return
	}
	items := make([]canvasArtifactResponse, 0, len(versions))
	for i := range versions {
		items = append(items, toCanvasArtifactResponse(&versions[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"versions": items})
}

// Revert — POST /conversations/{id}/artifacts/{key}/revert
// { "version": 2 }. Appends a NEW version copied from the named one —
// history stays intact (linear lineage, same as workspace rollback).
func (h *CanvasArtifactHandler) Revert(w http.ResponseWriter, r *http.Request) {
	caller, convID, ok := h.callerAndConv(w, r)
	if !ok {
		return
	}
	key, ok := artifactKeyParam(w, r)
	if !ok {
		return
	}
	var req revertCanvasArtifactRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Version < 1 {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "version must be >= 1")
		return
	}
	a, err := h.artifacts.Revert(r.Context(), caller.TenantUUID, caller.UserID, convID, key, req.Version)
	if err != nil {
		h.writeArtifactError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]any{"artifact": toCanvasArtifactResponse(a)})
}

// writeArtifactError maps service errors onto the wire contract.
// Ownership failures (ErrMessageConversationNotFound) are 404 like any
// other not-found — never 403, so existence isn't probeable.
func (h *CanvasArtifactHandler) writeArtifactError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrArtifactNotFound),
		errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, err.Error())
	case errors.Is(err, service.ErrArtifactTypeInvalid),
		errors.Is(err, service.ErrArtifactTooLarge),
		errors.Is(err, service.ErrArtifactKeyInvalid):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}
