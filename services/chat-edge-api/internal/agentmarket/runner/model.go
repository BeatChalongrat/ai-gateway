package runner

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ModelClient calls the model gateway with Agent Market's OWN API key — a
// separate identity with its own rate limit and spend, revocable on its own.
// It never borrows chat-edge's service credentials.
type ModelClient struct {
	BaseURL  string // e.g. http://gateway:8080/v1
	APIKey   string
	Primary  string
	Fallback string
	// VisionModel reads page images of scans and photos (AM2). Documents go
	// to it only when it is in SelfHosted, and never to a fallback.
	VisionModel string
	SelfHosted  map[string]bool
	Timeout     time.Duration
	MaxTokens   int
	HTTP        *http.Client
	// YieldURL (optional) is the backend /ready endpoint. Before every model
	// call the runner waits while people need the shared model path: a chat
	// turn is queued, chat already holds half the backend's turn slots, or the
	// backend's event loop is lagging. Agent runs sit below live chat.
	YieldURL     string
	YieldMaxWait time.Duration
	// YieldActive: wait while at least this many turns are generating (default 1).
	YieldActive int
	// Concurrency caps how many agent model calls are in flight at once
	// (whole runner, all slots): the agent's share of the backend stays one turn.
	Concurrency int

	semOnce sync.Once
	sem     chan struct{}
	// Yielded is the total time spent yielding to chat (reported per step).
	Yielded atomic.Int64
	// catalog is the gateway's model catalogue (AM4, choice.go).
	catalog catalogCache
}

// FallbackOptOut is the request header that tells the gateway never to answer
// with another model (AM4): a round's documents go to the model the
// organisation allowed and to no other, whatever the gateway's own rescue
// policy for that model.
const (
	FallbackOptOutHeader = "X-Sran-Fallback"
	FallbackOptOutValue  = "off"
)

func (m *ModelClient) acquire(ctx context.Context) (func(), error) {
	m.semOnce.Do(func() {
		n := m.Concurrency
		if n <= 0 {
			n = 1
		}
		m.sem = make(chan struct{}, n)
	})
	select {
	case m.sem <- struct{}{}:
		return func() { <-m.sem }, nil
	case <-ctx.Done():
		return func() {}, ctx.Err()
	}
}

// Completion is one answer.
type Completion struct {
	Model   string
	Content string
	// CostUSD / Tokens: from Complete, the total over every attempt.
	CostUSD    float64
	Tokens     int
	Cached     bool
	FinishedBy string
	Elapsed    time.Duration
}

type chatReq struct {
	Model       string  `json:"model"`
	Messages    any     `json:"messages"`
	MaxTokens   int     `json:"max_tokens,omitempty"`
	Temperature float64 `json:"temperature"`
	Stream      bool    `json:"stream"`
	Reasoning   *struct {
		Mode string `json:"mode"`
	} `json:"reasoning,omitempty"`
}

type chatMsg struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// ErrNoModel means no model answered (the caller falls back to rules).
var ErrNoModel = errors.New("no model answered")

// Available reports whether a model call can be attempted at all.
func (m *ModelClient) Available() bool {
	return m != nil && m.BaseURL != "" && m.APIKey != "" && m.Primary != ""
}

// Complete tries the primary model, then the fallback. A reply that is
// empty or cut off at the output limit counts as no answer (the owner's
// rule: the user never sees an empty answer — the caller then uses rules).
func (m *ModelClient) Complete(ctx context.Context, system, user string, validate func(string) error) (Completion, []string, error) {
	return m.CompleteWith(ctx, m.Models(), system, user, validate)
}

// Models is the primary then the fallback.
func (m *ModelClient) Models() []string {
	if m == nil || m.Primary == "" {
		return nil
	}
	models := []string{m.Primary}
	if m.Fallback != "" && m.Fallback != m.Primary {
		models = append(models, m.Fallback)
	}
	return models
}

// SelfHostedModels is Models() without any model that runs outside the organisation.
func (m *ModelClient) SelfHostedModels() []string {
	var out []string
	for _, x := range m.Models() {
		if m.SelfHosted[x] {
			out = append(out, x)
		}
	}
	return out
}

// CompleteWith is Complete over the given models, in order (AM3: an agent
// whose documents must stay in the organisation passes only self-hosted ones).
func (m *ModelClient) CompleteWith(ctx context.Context, models []string, system, user string, validate func(string) error) (Completion, []string, error) {
	var tried []string
	if !m.Available() || len(models) == 0 {
		return Completion{}, tried, ErrNoModel
	}
	release, err := m.acquire(ctx)
	if err != nil {
		return Completion{}, tried, err
	}
	defer release()
	// What the platform paid across every attempt (a refused answer was still
	// paid for); the budget is charged this, success or not.
	var spent float64
	var tokens int
	for _, model := range models {
		for attempt := 0; attempt < 2; attempt++ {
			m.yield(ctx)
			c, err := m.call(ctx, model, system, user)
			spent += c.CostUSD
			tokens += c.Tokens
			note := model
			if err != nil {
				tried = append(tried, note+": "+trim(err.Error(), 160))
				break // a transport/model failure: go to the next model
			}
			if validate != nil {
				if verr := validate(c.Content); verr != nil {
					tried = append(tried, note+": "+trim(verr.Error(), 160))
					// One retry on the same model with the reason, then move on.
					user = user + "\n\nคำตอบก่อนหน้าใช้ไม่ได้เพราะ: " + verr.Error() + " แก้แล้วตอบใหม่เป็น JSON ตามรูปแบบเดิม"
					continue
				}
			}
			c.CostUSD, c.Tokens = spent, tokens
			return c, tried, nil
		}
	}
	return Completion{CostUSD: spent, Tokens: tokens}, tried, ErrNoModel
}

// Vision asks one model to read page images (JPEG) with a prompt. Unlike
// Complete there is no fallback model: a document goes only to the model
// named. validate works as in Complete (one retry with the reason).
func (m *ModelClient) Vision(ctx context.Context, model, prompt string, images [][]byte, validate func(string) error) (Completion, error) {
	if !m.Available() || model == "" {
		return Completion{}, ErrNoModel
	}
	release, err := m.acquire(ctx)
	if err != nil {
		return Completion{}, err
	}
	defer release()
	var spent float64
	var tokens int
	text := prompt
	for attempt := 0; attempt < 2; attempt++ {
		parts := []map[string]any{{"type": "text", "text": text}}
		for _, img := range images {
			parts = append(parts, map[string]any{"type": "image_url",
				"image_url": map[string]any{"url": "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(img)}})
		}
		m.yield(ctx)
		c, err := m.send(ctx, model, []any{map[string]any{"role": "user", "content": parts}}, 0, 2000)
		spent += c.CostUSD
		tokens += c.Tokens
		if err != nil {
			return Completion{CostUSD: spent, Tokens: tokens}, err
		}
		if validate != nil {
			if verr := validate(c.Content); verr != nil {
				text = prompt + "\n\nคำตอบก่อนหน้าใช้ไม่ได้เพราะ: " + verr.Error() + " ตอบใหม่เป็น JSON object เดียว"
				continue
			}
		}
		c.CostUSD, c.Tokens = spent, tokens
		return c, nil
	}
	return Completion{CostUSD: spent, Tokens: tokens}, ErrNoModel
}

func (m *ModelClient) call(ctx context.Context, model, system, user string) (Completion, error) {
	return m.send(ctx, model, []chatMsg{{Role: "system", Content: system}, {Role: "user", Content: user}}, 0.2, m.MaxTokens)
}

func (m *ModelClient) send(ctx context.Context, model string, messages any, temperature float64, maxTokens int) (Completion, error) {
	ctx, cancel := context.WithTimeout(ctx, m.Timeout)
	defer cancel()
	body := chatReq{Model: model, Temperature: temperature, MaxTokens: maxTokens, Messages: messages}
	body.Reasoning = &struct {
		Mode string `json:"mode"`
	}{Mode: "off"}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(m.BaseURL, "/")+"/chat/completions", bytes.NewReader(b))
	if err != nil {
		return Completion{}, err
	}
	req.Header.Set("Authorization", "Bearer "+m.APIKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Client-Name", "agent-runner")
	req.Header.Set(FallbackOptOutHeader, FallbackOptOutValue)
	start := time.Now()
	resp, err := m.HTTP.Do(req)
	if err != nil {
		return Completion{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode >= 400 {
		return Completion{}, fmt.Errorf("gateway %d: %s", resp.StatusCode, trim(string(raw), 200))
	}
	var out struct {
		Model   string `json:"model"`
		Cached  bool   `json:"cached"`
		Choices []struct {
			Message struct {
				Content any `json:"content"`
			} `json:"message"`
			FinishReason string `json:"finish_reason"`
		} `json:"choices"`
		Usage struct {
			TotalTokens      int     `json:"total_tokens"`
			EstimatedCostUSD float64 `json:"estimated_cost_usd"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return Completion{}, fmt.Errorf("bad gateway response: %v", err)
	}
	if len(out.Choices) == 0 {
		return Completion{}, fmt.Errorf("no choices")
	}
	content := ""
	switch v := out.Choices[0].Message.Content.(type) {
	case string:
		content = v
	case []any:
		for _, part := range v {
			if p, ok := part.(map[string]any); ok {
				if t, ok := p["text"].(string); ok {
					content += t
				}
			}
		}
	}
	content = stripThinking(content)
	// A cache hit replays the original response, its cost included; the
	// gateway bills it as free, and so does the agent's budget.
	cost := out.Usage.EstimatedCostUSD
	if out.Cached {
		cost = 0
	}
	spent := Completion{CostUSD: cost, Tokens: out.Usage.TotalTokens}
	if strings.TrimSpace(content) == "" {
		return spent, fmt.Errorf("empty answer (finish=%s)", out.Choices[0].FinishReason)
	}
	if out.Choices[0].FinishReason == "length" {
		return spent, fmt.Errorf("answer cut off at the output limit")
	}
	used := out.Model
	if used == "" {
		used = model
	}
	if !sameModel(used, model) {
		// AM4: an answer from a model other than the one asked for is never
		// used — the organisation allowed that model, not this one.
		return spent, fmt.Errorf("answered by %s instead of %s", used, model)
	}
	return Completion{Model: used, Content: content, CostUSD: cost, Tokens: out.Usage.TotalTokens, Cached: out.Cached,
		FinishedBy: out.Choices[0].FinishReason, Elapsed: time.Since(start)}, nil
}

// sameModel compares a response's model with the one asked for (a gateway
// may add a provider prefix: "sranai/qwen3.8-27b-fp8").
func sameModel(got, want string) bool {
	g, w := strings.ToLower(strings.TrimSpace(got)), strings.ToLower(strings.TrimSpace(want))
	return g == w || strings.HasSuffix(g, "/"+w)
}

var thinkRe = regexp.MustCompile(`(?s)<think>.*?</think>`)

func stripThinking(s string) string { return strings.TrimSpace(thinkRe.ReplaceAllString(s, "")) }

// yield waits (bounded by YieldMaxWait) while people need the model path.
func (m *ModelClient) yield(ctx context.Context) {
	if m.YieldURL == "" {
		return
	}
	start := time.Now()
	defer func() { m.Yielded.Add(int64(time.Since(start) / time.Millisecond)) }()
	deadline := start.Add(m.YieldMaxWait)
	for time.Now().Before(deadline) {
		busy, ok := m.chatBusy(ctx)
		if !ok || !busy {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}

// chatBusy reads the backend's /ready: people queued, chat holding half the
// turn slots, or a lagging event loop all mean "not now".
func (m *ModelClient) chatBusy(ctx context.Context) (bool, bool) {
	cctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(cctx, http.MethodGet, m.YieldURL, nil)
	resp, err := m.HTTP.Do(req)
	if err != nil {
		return false, false
	}
	defer resp.Body.Close()
	var body struct {
		Checks struct {
			TurnQueue struct {
				Active    int `json:"active"`
				Waiting   int `json:"waiting"`
				MaxActive int `json:"max_active"`
			} `json:"turn_queue"`
			EventLoopLagMS float64 `json:"event_loop_lag_ms"`
		} `json:"checks"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&body) != nil {
		return false, false
	}
	q := body.Checks.TurnQueue
	limit := m.YieldActive
	if limit <= 0 {
		limit = 1
	}
	// Our own call is not in flight here (≤1 per runner), so every active
	// turn is someone else's: with the default limit of 1, an agent model
	// call starts only when no chat turn is generating.
	busy := q.Waiting > 0 || q.Active >= limit || body.Checks.EventLoopLagMS > 250
	return busy, true
}

func trim(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ── the number guard ─────────────────────────────────────────────────────

var numRe = regexp.MustCompile(`[-+]?\(?\d[\d,]*(?:\.\d+)?\)?(?:\s?%|\s?ล้าน)?`)

// GuardNumbers refuses any number in model prose that is not one of the
// computed table's pre-formatted strings. Small counts (≤ 31) and years are
// allowed; everything else must be quoted, character for character. ids are
// the account codes the model was shown: identifiers, skipped before the scan.
func GuardNumbers(text string, quotable map[string]string, ids []string) error {
	text = blankIDs(text, ids)
	allowed := map[string]bool{}
	for _, v := range quotable {
		for _, f := range numberForms(v) {
			allowed[f] = true
		}
	}
	var bad []string
	for _, m := range numRe.FindAllString(text, -1) {
		tok := normalizeNumberToken(m)
		if allowed[tok] || allowed[unsigned(tok)] {
			continue
		}
		plain := strings.Trim(tok, "+-()%ล้าน")
		if !strings.ContainsAny(plain, ",.") {
			var n int
			if _, err := fmt.Sscanf(plain, "%d", &n); err == nil && (n <= 31 || (n >= 2400 && n <= 2700) || (n >= 1990 && n <= 2100)) {
				continue
			}
		}
		bad = append(bad, m)
	}
	if len(bad) > 0 {
		return fmt.Errorf("ตัวเลขที่ไม่มีในตารางที่คำนวณ: %s", strings.Join(bad, ", "))
	}
	return nil
}

// numberForms are the ways a sentence quotes one of the table's figures: as
// printed, without its unit ("-0.95 จุด" → "-0.95": the number pattern knows
// % and ล้าน only), and without its sign or accounting brackets — the
// direction is said in words ("ต่ำกว่างบ 6.8%" for -6.8%). The digits must
// still be the table's own.
func numberForms(v string) []string {
	base := strings.ReplaceAll(v, " ", "")
	forms := []string{base}
	for _, unit := range []string{"จุด"} {
		if strings.HasSuffix(base, unit) {
			forms = append(forms, strings.TrimSuffix(base, unit))
		}
	}
	if strings.HasSuffix(base, "ล้าน") {
		forms = append(forms, strings.TrimSuffix(base, "ล้าน"))
	}
	out := make([]string, 0, len(forms)*2)
	for _, f := range forms {
		out = append(out, f, unsigned(f))
	}
	return out
}

// unsigned drops a leading sign and balanced accounting brackets.
func unsigned(tok string) string {
	t := strings.TrimLeft(tok, "+-")
	if strings.HasPrefix(t, "(") && strings.HasSuffix(t, ")") {
		t = strings.TrimSuffix(strings.TrimPrefix(t, "("), ")")
	}
	return t
}

// blankIDs removes the account codes from the text. A code with a separator
// ("5300-01") is blanked anywhere; one that is only digits ("510000") could
// pass for an amount, so it is blanked only right after บัญชี or รหัส.
func blankIDs(text string, ids []string) string {
	sorted := append([]string(nil), ids...)
	sort.Slice(sorted, func(i, j int) bool { return len(sorted[i]) > len(sorted[j]) })
	for _, id := range sorted {
		id = strings.TrimSpace(id)
		if id == "" {
			continue
		}
		if strings.ContainsAny(id, "-./") {
			text = strings.ReplaceAll(text, id, " ")
			continue
		}
		re := regexp.MustCompile(`(บัญชี|รหัส)\s*` + regexp.QuoteMeta(id) + `([^0-9.,]|$)`)
		text = re.ReplaceAllString(text, "$1 $2")
	}
	return text
}

// normalizeNumberToken drops spaces and an unbalanced parenthesis: only a
// balanced "(1,234)" is an accounting negative; "(12.48 ล้าน" is a number
// inside ordinary Thai parentheses.
func normalizeNumberToken(m string) string {
	tok := strings.ReplaceAll(m, " ", "")
	open, close := strings.HasPrefix(tok, "("), strings.HasSuffix(tok, ")")
	if open && !close {
		tok = tok[1:]
	} else if close && !open {
		tok = tok[:len(tok)-1]
	}
	return tok
}
