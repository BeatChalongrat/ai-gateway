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
// File inputs are optional and resolved by CALLER (tenant+user) ownership
// only — never another user's file. The route is ALWAYS registered so the
// client/server contract is stable; when the sandbox is disabled the
// interpreter is nil and Run answers 503 (feature off), not 404.
package handler

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// CodeHandler serves POST /code/run. Optional (nil when the sandbox is
// off); the router only registers the route when wired.
type CodeHandler struct {
	interp *service.CodeInterpreter
	files  *service.FileService
}

func NewCodeHandler(interp *service.CodeInterpreter, files *service.FileService) *CodeHandler {
	return &CodeHandler{interp: interp, files: files}
}

type runCodeRequest struct {
	Code  string   `json:"code"`
	Files []string `json:"files"`
}

type runCodeImage struct {
	Filename string `json:"filename"`
	DataURI  string `json:"data_uri"`
}

type runCodeFile struct {
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

	// Resolve + materialise optional file inputs — caller-owned only.
	var inputs []sandbox.InputFile
	var cleanups []func()
	defer func() {
		for _, cl := range cleanups {
			cl()
		}
	}()
	for _, raw := range req.Files {
		fid, err := uuid.Parse(raw)
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id: "+raw)
			return
		}
		path, name, cleanup, err := h.files.OpenUserFileCopy(r.Context(), caller.TenantUUID, caller.UserID, fid)
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
		if service.IsImageArtifact(art.Name) {
			resp.Images = append(resp.Images, runCodeImage{
				Filename: art.Name,
				DataURI:  "data:" + ct + ";base64," + base64.StdEncoding.EncodeToString(data),
			})
			continue
		}
		resp.Files = append(resp.Files, runCodeFile{
			Filename:    art.Name,
			ContentType: ct,
			SizeBytes:   len(data),
		})
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}
