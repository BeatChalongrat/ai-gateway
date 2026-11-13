// Code interpreter HTTP endpoint — Phase 13 Part B.
//
//	POST /api/v1/code/run   (behind RequireAuth, rate-limited)
//
// The desktop agent's thin-client RunPythonTool POSTs {code, files?} here;
// chat-edge runs the model code in the SAME hardened sandbox the chat
// run_python tool uses (no network, non-root, seccomp, read-only rootfs,
// stripped env, resource-capped) and returns stdout/stderr plus any
// produced artifacts. Images come back inline as base64 data URIs so the
// desktop can hand them to the model for vision; other files are
// summarised (a headless agent run has no conversation to attach them to).
//
// File inputs are optional and checked against CALLER (tenant+user) ownership.
// A named conversation additionally restricts inputs to that conversation.
// The route is ALWAYS registered so the
// client/server contract is stable; when the sandbox is disabled the
// interpreter is nil and Run answers 503 (feature off), not 404.
package handler

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// codeRunFiles is the slice of *service.FileService this handler needs —
// an interface so the FIX80 persistence path is unit-testable without a
// database or a storage backend.
type codeRunFiles interface {
	OpenUserFileCopy(ctx context.Context, tenantID, userID, fileID uuid.UUID) (path, name string, cleanup func(), err error)
	OpenConversationFileCopy(ctx context.Context, tenantID, userID, convID, fileID uuid.UUID) (path, name string, cleanup func(), err error)
	AssertConversationOwned(ctx context.Context, tenantID, userID, convID uuid.UUID) error
	SaveGeneratedFile(ctx context.Context, in service.GeneratedFileInput) (*model.ConversationFile, error)
}

// CodeHandler serves POST /code/run. Optional (nil when the sandbox is
// off); the router only registers the route when wired.
type CodeHandler struct {
	interp *service.CodeInterpreter
	files  codeRunFiles
}

func NewCodeHandler(interp *service.CodeInterpreter, files codeRunFiles) *CodeHandler {
	return &CodeHandler{interp: interp, files: files}
}

type runCodeRequest struct {
	Code  string   `json:"code"`
	Files []string `json:"files"`
	// ConversationID (FIX80, optional) — when the desktop names its
	// conversation, every produced artifact is ALSO persisted as a
	// ConversationFile (ownership-checked), so a chart or an .xlsx the
	// sandbox made has a durable, downloadable home in the chat instead
	// of living only in an inline data URI. Empty keeps the old
	// return-inline-and-discard behaviour byte-identical.
	ConversationID string `json:"conversation_id,omitempty"`
}

type runCodeImage struct {
	// ID is the persisted ConversationFile id when a conversation was
	// named (FIX80); empty otherwise.
	ID       string `json:"id,omitempty"`
	Filename string `json:"filename"`
	DataURI  string `json:"data_uri"`
}

type runCodeFile struct {
	ID          string `json:"id,omitempty"`
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	SizeBytes   int    `json:"size_bytes"`
}

type runCodeResponse struct {
	Exit      int            `json:"exit"`
	Stdout    string         `json:"stdout"`
	Stderr    string         `json:"stderr"`
	TimedOut  bool           `json:"timed_out"`
	Truncated bool           `json:"truncated"`
	Images    []runCodeImage `json:"images"`
	Files     []runCodeFile  `json:"files"`
}

const (
	maxCodeRequestBytes  = 512 << 10
	maxCodeResponseFiles = 8
)

func (h *CodeHandler) Run(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())

	// The route is always registered so the contract between the desktop
	// client and this service is stable; when the sandbox is disabled
	// (SANDBOX_ENABLED=0) the interpreter is nil and we answer 503, never
	// 404 — a 503 tells the client "endpoint exists, feature off" rather
	// than "unknown server". No model code runs in-process here.
	if h.interp == nil {
		WriteError(w, http.StatusServiceUnavailable, CodeInternalError, "code interpreter is disabled on this server")
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxCodeRequestBytes)
	var req runCodeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Code == "" {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "code is required")
		return
	}
	// FIX80 — resolve the optional conversation before running anything:
	// a bad id must fail loudly, not silently drop the user's artifacts.
	var convID uuid.UUID
	if req.ConversationID != "" {
		cid, err := uuid.Parse(req.ConversationID)
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation_id")
			return
		}
		if err := h.files.AssertConversationOwned(r.Context(), caller.TenantUUID, caller.UserID, cid); err != nil {
			if errors.Is(err, service.ErrFileConvNotFound) {
				WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
				return
			}
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "could not verify conversation")
			return
		}
		convID = cid
	}

	// Resolve + materialise optional file inputs — caller-owned only.
	var inputs []sandbox.InputFile
	var cleanups []func()
	defer func() {
		for _, cl := range cleanups {
			cl()
		}
	}()
	seen := make(map[uuid.UUID]bool)
	for _, raw := range req.Files {
		fid, err := uuid.Parse(strings.TrimSpace(raw))
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id: "+raw)
			return
		}
		if seen[fid] {
			continue
		}
		seen[fid] = true
		var path, name string
		var cleanup func()
		if convID != uuid.Nil {
			path, name, cleanup, err = h.files.OpenConversationFileCopy(r.Context(), caller.TenantUUID, caller.UserID, convID, fid)
		} else {
			path, name, cleanup, err = h.files.OpenUserFileCopy(r.Context(), caller.TenantUUID, caller.UserID, fid)
		}
		if err != nil {
			if errors.Is(err, service.ErrFileNotFound) {
				WriteError(w, http.StatusNotFound, CodeNotFound, "file not found: "+fid.String())
				return
			}
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "could not open file")
			return
		}
		cleanups = append(cleanups, cleanup)
		inputs = append(inputs, sandbox.InputFile{Name: name, LocalPath: path})
	}

	res, err := h.interp.Run(r.Context(), req.Code, inputs)
	if err != nil {
		if errors.Is(err, sandbox.ErrUnavailable) {
			WriteError(w, http.StatusServiceUnavailable, CodeInternalError, "code interpreter unavailable")
			return
		}
		if res != nil {
			res.Cleanup()
		}
		WriteError(w, http.StatusBadGateway, CodeInternalError, "code interpreter error: "+err.Error())
		return
	}
	defer res.Cleanup()

	resp := runCodeResponse{
		Exit:      res.Exit,
		Stdout:    string(res.Stdout),
		Stderr:    string(res.Stderr),
		TimedOut:  res.TimedOut,
		Truncated: res.Truncated,
		Images:    []runCodeImage{},
		Files:     []runCodeFile{},
	}
	for i := range res.Artifacts {
		if len(resp.Images)+len(resp.Files) >= maxCodeResponseFiles {
			break
		}
		art := &res.Artifacts[i]
		data, rerr := service.ReadArtifact(art.Path)
		if rerr != nil {
			continue
		}
		ct := service.ArtifactContentType(art.Name)
		// FIX80 — with a conversation named, persist the artifact through
		// the SAME pipeline web run_python uses (SaveGeneratedFile), so the
		// desktop's chart or .xlsx becomes a durable, downloadable
		// ConversationFile instead of an inline-only data URI (images) or
		// discarded bytes (everything else). MessageID stays nil here — the
		// desktop links the file to its assistant row at import (FIX 10
		// file_ids). A failed save degrades to the old inline behaviour.
		var fileID string
		if convID != uuid.Nil {
			if saved, serr := h.files.SaveGeneratedFile(r.Context(), service.GeneratedFileInput{
				TenantID:         caller.TenantUUID,
				UserID:           caller.UserID,
				ConversationID:   convID,
				Data:             data,
				ContentType:      ct,
				OriginalFilename: art.Name,
			}); serr == nil {
				fileID = saved.ID.String()
			}
		}
		if service.IsImageArtifact(art.Name) {
			resp.Images = append(resp.Images, runCodeImage{
				ID:       fileID,
				Filename: art.Name,
				DataURI:  "data:" + ct + ";base64," + base64.StdEncoding.EncodeToString(data),
			})
			continue
		}
		resp.Files = append(resp.Files, runCodeFile{
			ID:          fileID,
			Filename:    art.Name,
			ContentType: ct,
			SizeBytes:   len(data),
		})
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}
