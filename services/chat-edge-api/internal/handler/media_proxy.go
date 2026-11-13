package handler

// Media direct-dispatch proxy — Sprint 2A.5.
//
// The web-chat media gen forms (VideoGenForm / AudioGenForm) POST to
// chat-edge; these endpoints live on the BACKEND gateway. This handler is a
// thin trusted proxy: it forwards the request to the gateway's PUBLIC media
// route carrying the caller's identity via the internal-trust header set
// (the same one the generate_video tool uses), and relays the gateway's
// response verbatim. The gateway enforces auth, the MEDIA_GEN DLP profile,
// the balance check, and the VIDEO_GEN_ENABLED feature gate.
//
//	POST /api/v1/videos/generations -> gateway POST /v1/videos/generations
//	POST /api/v1/audio/generations  -> gateway POST /v1/audio/generations
//	GET  /api/v1/jobs/{id}          -> gateway GET  /v1/jobs/{id}
//	POST /api/v1/jobs/{id}/cancel   -> gateway POST /v1/jobs/{id}/cancel
//	GET  /api/v1/jobs/{id}/output   -> gateway GET  /v1/jobs/{id}/output (streamed)

import (
	"encoding/json"
	"github.com/google/uuid"
	"io"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// maxMediaProxyBody caps the request body we buffer before forwarding —
// these are small JSON payloads (prompt + params), not file uploads.
const maxMediaProxyBody = 1 << 20 // 1 MiB

type MediaProxyHandler struct {
	gw         *service.GatewayClient
	videoModel string
}

func NewMediaProxyHandler(gw *service.GatewayClient, model ...string) *MediaProxyHandler {
	h := &MediaProxyHandler{gw: gw}
	if len(model) > 0 {
		h.videoModel = model[0]
	}
	return h
}

func (h *MediaProxyHandler) callCtx(r *http.Request) service.GatewayCallContext {
	caller, _ := middleware.CallerFrom(r.Context())
	return service.GatewayCallContext{
		TenantID: caller.TenantUUID,
		UserID:   caller.UserID,
	}
}

func (h *MediaProxyHandler) forward(
	w http.ResponseWriter, r *http.Request, method, subpath string,
) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(io.LimitReader(r.Body, maxMediaProxyBody))
	}
	status, respBody, err := h.gw.ProxyPublicMedia(
		r.Context(), h.callCtx(r), method, subpath, body,
	)
	if err != nil {
		WriteError(
			w, http.StatusBadGateway, "MEDIA_PROXY_FAILED",
			"media generation service unavailable",
		)
		return
	}
	// Relay the gateway's status + JSON envelope verbatim (incl. its error
	// responses — 400/402/403 from DLP / balance / feature gate).
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(respBody)
}

// SubmitVideo proxies POST /videos/generations.
func (h *MediaProxyHandler) VideoConfig(w http.ResponseWriter, r *http.Request) {
	status, body, err := h.gw.ProxyPublicMedia(r.Context(), h.callCtx(r), http.MethodGet, "/video/config", nil)
	if err != nil {
		WriteError(w, 502, CodeInternalError, "media service unavailable")
		return
	}
	var config map[string]any
	if status == 200 && json.Unmarshal(body, &config) == nil {
		config["agent_model"] = h.videoModel
		body, _ = json.Marshal(config)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func (h *MediaProxyHandler) SubmitVideo(w http.ResponseWriter, r *http.Request) {
	h.forward(w, r, http.MethodPost, "/videos/generations")
}

// SubmitAudio proxies POST /audio/generations.
func (h *MediaProxyHandler) SubmitAudio(w http.ResponseWriter, r *http.Request) {
	h.forward(w, r, http.MethodPost, "/audio/generations")
}

// GetJob proxies GET /jobs/{id}.
func (h *MediaProxyHandler) GetJob(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	h.forward(w, r, http.MethodGet, "/jobs/"+id)
}

// CancelJob proxies POST /jobs/{id}/cancel (Sprint 3 — abort + refund).
func (h *MediaProxyHandler) ConfirmJob(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	h.forward(w, r, http.MethodPost, "/jobs/"+id+"/confirm")
}

func (h *MediaProxyHandler) CancelJob(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	h.forward(w, r, http.MethodPost, "/jobs/"+id+"/cancel")
}

// JobOutput proxies GET /jobs/{id}/output — the finished artifact bytes.
// Phase 11 A3: web-chat plays generated video/audio through the authed
// client (blob → object URL), so the artifact must be reachable on the
// chat-edge origin with cookie auth. Unlike the JSON proxies above this
// STREAMS the upstream body (artifacts can run to tens of MB) and relays
// the media headers instead of stamping application/json.
func (h *MediaProxyHandler) JobOutput(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	resp, err := h.gw.ProxyPublicMediaOutput(
		r.Context(), h.callCtx(r), "/jobs/"+id+"/output",
	)
	if err != nil {
		WriteError(
			w, http.StatusBadGateway, "MEDIA_PROXY_FAILED",
			"media generation service unavailable",
		)
		return
	}
	defer resp.Body.Close()
	for _, k := range []string{"Content-Type", "Content-Length", "Content-Disposition"} {
		if v := resp.Header.Get(k); v != "" {
			w.Header().Set(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

func (h *MediaProxyHandler) ListJobs(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(r.URL.Query().Get("conversation_id"))
	if err != nil {
		WriteError(w, 400, CodeBadRequest, "invalid conversation id")
		return
	}
	h.forward(w, r, http.MethodGet, "/jobs?conversation_id="+id.String())
}
