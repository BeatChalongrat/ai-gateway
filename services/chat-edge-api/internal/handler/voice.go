// Voice — thin OpenAI proxies for speech-to-text (Whisper) and
// text-to-speech (TTS).
//
// Two routes:
//
//	POST /api/v1/voice/transcribe   multipart audio → { "text": "..." }
//	POST /api/v1/voice/synthesize   { text, voice, speed } → audio/mpeg
//
// Both require a configured OPENAI_API_KEY (same env var the
// embedding service reads). When unset, both endpoints return 503
// with a clear "configure OpenAI" error so the UI can hide the
// premium-quality options instead of failing silently inside the
// MediaRecorder upload path.
//
// Why proxy instead of calling OpenAI directly from the browser?
// Two reasons:
//   - The OpenAI key never leaks to the client.
//   - We can apply tenant-scoped rate limiting in a future Phase 4
//     without re-architecting the call site.
package handler

import (
	"bytes"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"strconv"
	"time"
)

// VoiceHandler holds the upstream config. The OpenAI key is read at
// construction time so mid-process env flips don't change behaviour —
// matches the embedding/RAG service pattern.
type VoiceHandler struct {
	apiKey       string
	baseURL      string
	whisperModel string
	ttsModel     string
	http         *http.Client
	// Per-request body limit. Whisper accepts up to 25 MB.
	maxAudioBytes int64
}

func NewVoiceHandler() *VoiceHandler {
	base := os.Getenv("OPENAI_BASE_URL")
	if base == "" {
		base = "https://api.openai.com/v1"
	}
	wm := os.Getenv("OPENAI_WHISPER_MODEL")
	if wm == "" {
		wm = "whisper-1"
	}
	tm := os.Getenv("OPENAI_TTS_MODEL")
	if tm == "" {
		tm = "tts-1"
	}
	return &VoiceHandler{
		apiKey:        os.Getenv("OPENAI_API_KEY"),
		baseURL:       base,
		whisperModel:  wm,
		ttsModel:      tm,
		http:          &http.Client{Timeout: 90 * time.Second},
		maxAudioBytes: 25 * 1024 * 1024,
	}
}

func (h *VoiceHandler) enabled() bool { return h.apiKey != "" }

// ---------------------------------------------------------------------------
// Transcribe
// ---------------------------------------------------------------------------

// Allow-list of audio mime types we forward. Whisper itself supports
// a much wider list (flac, m4a, mp4, mpeg, mpga, ogg, wav, webm) but
// pinning to what MediaRecorder + iOS Safari actually produce keeps
// the surface tight.
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
			"voice service not configured (OPENAI_API_KEY)")
		return
	}
	// 32 MiB in-memory ceiling — slightly above the 25 MB Whisper
	// limit so headers + form overhead fit without spilling to disk.
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

	// language is an optional Whisper hint — auto-detect when blank.
	language := r.FormValue("language")

	// Build the multipart body for the OpenAI request. We can't just
	// pipe the incoming form through — OpenAI wants `model` + `file`
	// (and optionally `language`), not the raw `audio` field name.
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	if err := mw.WriteField("model", h.whisperModel); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "encode form")
		return
	}
	if language != "" {
		_ = mw.WriteField("language", language)
	}
	part, err := mw.CreateFormFile("file", chooseFilename(header.Filename, ct))
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "encode form")
		return
	}
	// Cap the copy at maxAudioBytes as a defence-in-depth check: the
	// Content-Length header above could lie about the size.
	if _, err := io.Copy(part, io.LimitReader(file, h.maxAudioBytes+1)); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "read audio")
		return
	}
	if err := mw.Close(); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "encode form")
		return
	}

	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost,
		h.baseURL+"/audio/transcriptions", &body)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "build upstream request")
		return
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+h.apiKey)
	resp, err := h.http.Do(req)
	if err != nil {
		WriteError(w, http.StatusBadGateway, "UPSTREAM_UNAVAILABLE",
			"voice provider unreachable")
		return
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		WriteError(w, http.StatusBadGateway, "UPSTREAM_ERROR",
			truncateVoice(string(respBody), 200))
		return
	}
	// OpenAI's default response is { "text": "..." } — pass through
	// unchanged, but defensively re-encode so we don't forward an
	// unexpected response shape.
	var parsed struct {
		Text string `json:"text"`
	}
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		WriteError(w, http.StatusBadGateway, "UPSTREAM_ERROR", "unexpected upstream response")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"text": parsed.Text})
}

// chooseFilename derives a filename Whisper will accept. OpenAI uses
// the file extension to pick the decoder, so we map content-type
// back to an extension instead of forwarding the user's filename
// (which is often "blob" when MediaRecorder records into memory).
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
	if original == "" || original == "blob" {
		return "audio." + ext
	}
	// Force the right extension regardless of what the client sent.
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

// OpenAI's documented voice options. Anything outside this set
// produces a 400 from OpenAI; we pre-validate to give a cleaner
// error and avoid the upstream round-trip.
var allowedTTSVoices = map[string]bool{
	"alloy":   true,
	"echo":    true,
	"fable":   true,
	"onyx":    true,
	"nova":    true,
	"shimmer": true,
}

const maxTTSChars = 4096

func (h *VoiceHandler) Synthesize(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		WriteError(w, http.StatusServiceUnavailable, "VOICE_DISABLED",
			"voice service not configured (OPENAI_API_KEY)")
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
			"text too long (max "+strconv.Itoa(maxTTSChars)+" chars)")
		return
	}
	if req.Voice == "" {
		req.Voice = "alloy"
	}
	if !allowedTTSVoices[req.Voice] {
		WriteError(w, http.StatusBadRequest, CodeValidationError, "unknown voice")
		return
	}
	if req.Speed == 0 {
		req.Speed = 1.0
	}
	if req.Speed < 0.25 {
		req.Speed = 0.25
	}
	if req.Speed > 4.0 {
		req.Speed = 4.0
	}

	upstream, err := json.Marshal(map[string]any{
		"model":           h.ttsModel,
		"voice":           req.Voice,
		"input":           req.Text,
		"speed":           req.Speed,
		"response_format": "mp3",
	})
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "encode upstream body")
		return
	}
	hreq, err := http.NewRequestWithContext(r.Context(), http.MethodPost,
		h.baseURL+"/audio/speech", bytes.NewReader(upstream))
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "build upstream request")
		return
	}
	hreq.Header.Set("Content-Type", "application/json")
	hreq.Header.Set("Authorization", "Bearer "+h.apiKey)
	resp, err := h.http.Do(hreq)
	if err != nil {
		WriteError(w, http.StatusBadGateway, "UPSTREAM_UNAVAILABLE",
			"voice provider unreachable")
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		WriteError(w, http.StatusBadGateway, "UPSTREAM_ERROR",
			truncateVoice(string(body), 200))
		return
	}
	// Stream the audio body straight back to the client. We don't
	// know Content-Length up front (the upstream uses chunked
	// transfer), so we copy until EOF and let the HTTP/1.1 layer pick
	// the right framing.
	w.Header().Set("Content-Type", "audio/mpeg")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	if _, err := io.Copy(w, resp.Body); err != nil {
		// Connection broken mid-stream; nothing useful to surface.
		// Logging happens at the access-log layer.
		_ = err
	}
}

// truncateVoice keeps upstream error bodies from leaking unbounded
// content into our error envelope.
func truncateVoice(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
