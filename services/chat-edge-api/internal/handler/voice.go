// Voice — speech-to-text (Whisper) and text-to-speech (TTS) proxied
// through the upstream Gateway.
//
// Two routes:
//
//	POST /api/v1/voice/transcribe   multipart audio → { "text": "..." }
//	POST /api/v1/voice/synthesize   { text, voice, speed } → audio/mpeg
//
// FIX 2: these used to call OpenAI directly, which bypassed DLP — raw
// voice audio and TTS text egressed to OpenAI unmasked. They now forward
// to the Gateway (/v1/voice/*), which runs the raw-egress gate
// (assert_stt_egress_allowed for audio; scan_voice_text for TTS text —
// credential-block + per-tenant PII + audit) before calling OpenAI. The
// handler no longer holds an OpenAI key; it holds a *GatewayClient and
// forwards the authenticated end-user identity so the gateway can
// attribute + audit the egress.
//
// When no gateway is wired both endpoints return 503 with a clear
// "voice not configured" error so the UI can hide the premium options.
package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// VoiceHandler forwards voice requests to the gateway as a trusted
// internal caller carrying the end-user identity.
type VoiceHandler struct {
	gw *service.GatewayClient
	// Per-request body limit. Whisper accepts up to 25 MB.
	maxAudioBytes int64
}

func NewVoiceHandler(gw *service.GatewayClient) *VoiceHandler {
	return &VoiceHandler{
		gw:            gw,
		maxAudioBytes: 25 * 1024 * 1024,
	}
}

func (h *VoiceHandler) enabled() bool { return h.gw != nil }

func (h *VoiceHandler) callCtx(r *http.Request) service.GatewayCallContext {
	caller, _ := middleware.CallerFrom(r.Context())
	return service.GatewayCallContext{
		TenantID: caller.TenantUUID,
		UserID:   caller.UserID,
	}
}

// ---------------------------------------------------------------------------
// Transcribe
// ---------------------------------------------------------------------------

// Allow-list of audio mime types we forward. Whisper supports more, but
// pinning to what MediaRecorder + iOS Safari actually produce keeps the
// surface tight. (The gateway does its own validation too.)
var allowedAudioTypes = map[string]bool{
	"audio/webm":               true,
	"audio/webm;codecs=opus":   true,
	"audio/mp4":                true,
	"audio/mpeg":               true,
	"audio/mp3":                true,
	"audio/wav":                true,
	"audio/x-wav":              true,
	"audio/ogg":                true,
	"audio/ogg;codecs=opus":    true,
	"application/octet-stream": true, // some recorders omit the codec
}

func (h *VoiceHandler) Transcribe(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		WriteError(w, http.StatusServiceUnavailable, "VOICE_DISABLED",
			"voice service not configured")
		return
	}
	// 32 MiB in-memory ceiling — slightly above the 25 MB Whisper limit so
	// headers + form overhead fit without spilling to disk.
	if err := r.ParseMultipartForm(32 * 1024 * 1024); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid multipart body")
		return
	}
	file, header, err := r.FormFile("audio")
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "missing 'audio' field")
		return
	}
	defer file.Close()
	if header.Size > h.maxAudioBytes {
		WriteError(w, http.StatusRequestEntityTooLarge, CodeValidationError,
			"audio too large (max 25 MB)")
		return
	}
	ct := header.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/octet-stream"
	}
	if !allowedAudioTypes[ct] {
		WriteError(w, http.StatusUnsupportedMediaType, CodeValidationError,
			"audio type not allowed (use webm, mp4, mp3, wav, or ogg)")
		return
	}

	// Read the audio (bounded) and forward to the gateway. Cap at
	// maxAudioBytes+1 as defence-in-depth against a lying Content-Length.
	audio, err := io.ReadAll(io.LimitReader(file, h.maxAudioBytes+1))
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "read audio")
		return
	}
	if int64(len(audio)) > h.maxAudioBytes {
		WriteError(w, http.StatusRequestEntityTooLarge, CodeValidationError,
			"audio too large (max 25 MB)")
		return
	}

	// language is an optional Whisper hint — auto-detect when blank.
	language := r.FormValue("language")

	text, err := h.gw.VoiceTranscribe(
		r.Context(), h.callCtx(r), audio,
		chooseFilename(header.Filename, ct), ct, language,
	)
	if err != nil {
		h.writeGatewayError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"text": text})
}

// chooseFilename derives a filename the gateway/Whisper will accept. The
// decoder is picked from the extension, so we map content-type back to an
// extension instead of forwarding the user's filename (often "blob").
func chooseFilename(original, ct string) string {
	ext := ""
	switch ct {
	case "audio/webm", "audio/webm;codecs=opus":
		ext = "webm"
	case "audio/mp4":
		ext = "mp4"
	case "audio/mpeg", "audio/mp3":
		ext = "mp3"
	case "audio/wav", "audio/x-wav":
		ext = "wav"
	case "audio/ogg", "audio/ogg;codecs=opus":
		ext = "ogg"
	default:
		ext = "webm"
	}
	return "audio." + ext
}

// ---------------------------------------------------------------------------
// Synthesize
// ---------------------------------------------------------------------------

type synthesizeRequest struct {
	Text  string  `json:"text"`
	Voice string  `json:"voice"`
	Speed float64 `json:"speed"`
}

const maxTTSChars = 4096

func (h *VoiceHandler) Synthesize(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		WriteError(w, http.StatusServiceUnavailable, "VOICE_DISABLED",
			"voice service not configured")
		return
	}
	var req synthesizeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Text == "" {
		WriteError(w, http.StatusBadRequest, CodeValidationError, "text is required")
		return
	}
	if len(req.Text) > maxTTSChars {
		WriteError(w, http.StatusBadRequest, CodeValidationError,
			"text too long (max 4096 chars)")
		return
	}
	if req.Voice == "" {
		req.Voice = "alloy"
	}
	if req.Speed == 0 {
		req.Speed = 1.0
	}

	audio, err := h.gw.VoiceSynthesize(r.Context(), h.callCtx(r), req.Text, req.Voice, req.Speed)
	if err != nil {
		h.writeGatewayError(w, err)
		return
	}
	// Stream the audio body straight back to the client.
	w.Header().Set("Content-Type", "audio/mpeg")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(audio)
}

// writeGatewayError maps a gateway call failure to the client. A gateway
// DLP block (403) or "disabled" (503) is surfaced with its upstream body
// so the caller sees why; transport failures map to 502.
func (h *VoiceHandler) writeGatewayError(w http.ResponseWriter, err error) {
	var gu *service.GatewayUnavailableError
	if errors.As(err, &gu) {
		switch {
		case gu.Status == http.StatusForbidden:
			WriteError(w, http.StatusForbidden, "VOICE_BLOCKED",
				"voice content blocked by DLP policy")
			return
		case gu.Status == http.StatusServiceUnavailable:
			WriteError(w, http.StatusServiceUnavailable, "VOICE_DISABLED",
				"voice service not configured")
			return
		case gu.Status >= 400 && gu.Status < 500:
			WriteError(w, gu.Status, CodeValidationError, truncateVoice(gu.Body, 200))
			return
		}
	}
	WriteError(w, http.StatusBadGateway, "UPSTREAM_UNAVAILABLE", "voice provider unreachable")
}

// truncateVoice keeps upstream error bodies from leaking unbounded content
// into our error envelope.
func truncateVoice(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
