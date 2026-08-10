// Cross-service route contract tests (Ecosystem Fix — Phase 11 Part C).
//
// These tests fail when a CLIENT and this SERVER disagree about which
// endpoints exist:
//
//   - web-chat  — every path the browser client (apps/web-chat/src/lib/api.ts)
//     calls is extracted from the TypeScript source and checked, method-aware,
//     against the routes actually registered by router.New.
//   - desktop   — every "api/v1/..." literal in the SranDesktop C# HTTP client
//     (SranDesktop/src/SranDesktop.Infrastructure) is extracted the same way.
//
// The route table is NOT parsed from router.go source — the real router is
// constructed (with zero-value handlers, which is safe because no handler is
// ever invoked) and enumerated with chi.Walk, so the check can never drift
// from what chi actually serves.
//
// REQUIREMENT: these tests read client sources out of the monorepo working
// tree via relative paths (../../../../apps/... and ../../../../SranDesktop/...).
// They therefore need the full monorepo checkout — which CI has (the go-test
// job runs `go test ./...` from services/chat-edge-api after a full
// actions/checkout). If a client tree is missing the test is skipped, not
// failed, so the Go module still tests standalone.
package router_test

import (
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/router"
)

// ---------------------------------------------------------------------------
// Route table — built from the REAL router.
// ---------------------------------------------------------------------------

type routeKey struct {
	method  string
	pattern string // params normalized to {param}
}

var paramSeg = regexp.MustCompile(`\{[^/}]+\}`)

// normalizePattern rewrites every named path parameter ({id}, {convID},
// {messageID}, {token}, ...) to the canonical {param} and trims a trailing
// slash so client paths and chi patterns compare structurally.
func normalizePattern(p string) string {
	p = paramSeg.ReplaceAllString(p, "{param}")
	if len(p) > 1 {
		p = strings.TrimSuffix(p, "/")
	}
	return p
}

// buildRouteTable constructs the production router with every optional
// handler wired (zero values — handlers are never called, only registered)
// and walks it. This is the server side of the contract.
func buildRouteTable(t *testing.T) map[routeKey]bool {
	t.Helper()

	h := router.Handlers{
		Auth:           &handler.AuthHandler{},
		Conversation:   &handler.ConversationHandler{},
		Message:        &handler.MessageHandler{},
		Model:          &handler.ModelHandler{},
		Feedback:       &handler.FeedbackHandler{},
		File:           &handler.FileHandler{},
		Preferences:    &handler.PreferencesHandler{},
		Share:          &handler.ShareHandler{},
		Admin:          &handler.AdminHandler{},
		Knowledge:      &handler.KnowledgeHandler{},
		Voice:          &handler.VoiceHandler{},
		Excel:          &handler.ExcelHandler{},
		Document:       &handler.DocumentHandler{},
		Workspace:      &handler.WorkspaceHandler{},
		Memory:         &handler.MemoryHandler{},
		Search:         &handler.SearchHandler{},
		Account:        &handler.AccountHandler{},
		Usage:          &handler.UsageHandler{},
		MediaProxy:     &handler.MediaProxyHandler{},
		Import:         &handler.ImportHandler{},
		Code:           &handler.CodeHandler{},
		Project:        &handler.ProjectHandler{},
		CanvasArtifact: &handler.CanvasArtifactHandler{},
	}

	mux := router.New(&config.Config{}, h, "", nil, nil)
	routes, ok := mux.(chi.Routes)
	if !ok {
		t.Fatalf("router.New no longer returns a chi router — contract test needs chi.Walk")
	}

	table := map[routeKey]bool{}
	err := chi.Walk(routes, func(method, pattern string, _ http.Handler, _ ...func(http.Handler) http.Handler) error {
		table[routeKey{method: method, pattern: normalizePattern(pattern)}] = true
		return nil
	})
	if err != nil {
		t.Fatalf("chi.Walk: %v", err)
	}
	if len(table) == 0 {
		t.Fatal("chi.Walk enumerated zero routes")
	}
	return table
}

// matchRoute reports whether a client's (method, path) resolves to a
// registered route. A route {param} segment matches any client segment;
// a client {param} segment matches ONLY a route {param} segment (a client
// templating a segment the server treats as static is real drift).
func matchRoute(table map[routeKey]bool, method, path string) bool {
	if table[routeKey{method, path}] {
		return true
	}
	want := strings.Split(path, "/")
	for rk := range table {
		if rk.method != method {
			continue
		}
		have := strings.Split(rk.pattern, "/")
		if len(have) != len(want) {
			continue
		}
		ok := true
		for i := range have {
			if have[i] == "{param}" {
				if want[i] == "" {
					ok = false
					break
				}
				continue
			}
			if have[i] != want[i] {
				ok = false
				break
			}
		}
		if ok {
			return true
		}
	}
	return false
}

// repoPath resolves a path relative to the monorepo root (this package sits
// at services/chat-edge-api/internal/router — four levels down).
func repoPath(parts ...string) string {
	return filepath.Join(append([]string{"..", "..", "..", ".."}, parts...)...)
}

type clientCall struct {
	method string
	path   string // /api/v1-prefixed, params normalized to {param}
	where  string // provenance for the failure message
}

func sortCalls(calls []clientCall) {
	sort.Slice(calls, func(i, j int) bool {
		if calls[i].path != calls[j].path {
			return calls[i].path < calls[j].path
		}
		return calls[i].method < calls[j].method
	})
}

// ---------------------------------------------------------------------------
// web-chat client extraction (apps/web-chat/src/lib/api.ts)
// ---------------------------------------------------------------------------

// scanJSString extracts the raw contents of the JS string/template literal
// starting at src[start] (a backtick, " or '). Template ${...} interpolations
// are copied verbatim, including nested template literals. Returns the raw
// contents and the index of the closing delimiter.
func scanJSString(src string, start int) (string, int) {
	delim := src[start]
	var b strings.Builder
	i := start + 1
	for i < len(src) {
		c := src[i]
		if c == '\\' && i+1 < len(src) {
			b.WriteByte(c)
			b.WriteByte(src[i+1])
			i += 2
			continue
		}
		if c == delim {
			return b.String(), i
		}
		if delim == '`' && c == '$' && i+1 < len(src) && src[i+1] == '{' {
			j := i + 2
			depth := 1
			for j < len(src) && depth > 0 {
				switch src[j] {
				case '{':
					depth++
				case '}':
					depth--
				case '`':
					// nested template inside the interpolation
					_, end := scanJSString(src, j)
					j = end
				}
				j++
			}
			b.WriteString(src[i:j])
			i = j
			continue
		}
		b.WriteByte(c)
		i++
	}
	return b.String(), len(src) - 1
}

// normalizeClientPath turns a raw template path into a comparable pattern:
//   - stops at the query string ('?')
//   - a ${...} interpolation directly after '/' becomes {param}
//   - a ${...} appended mid-segment (e.g. `/conversations${q}`) is a
//     query/suffix appendage — the path is truncated there
//
// Returns "" for paths with no usable static prefix.
func normalizeClientPath(raw string) string {
	var b strings.Builder
	i := 0
	for i < len(raw) {
		c := raw[i]
		if c == '?' {
			break
		}
		if c == '$' && i+1 < len(raw) && raw[i+1] == '{' {
			j := i + 2
			depth := 1
			for j < len(raw) && depth > 0 {
				switch raw[j] {
				case '{':
					depth++
				case '}':
					depth--
				case '`':
					_, end := scanJSString(raw, j)
					j = end
				}
				j++
			}
			cur := b.String()
			if strings.HasSuffix(cur, "/") {
				b.WriteString("{param}")
				i = j
				continue
			}
			break // mid-segment appendage → truncate
		}
		b.WriteByte(c)
		i++
	}
	p := b.String()
	if len(p) > 1 {
		p = strings.TrimSuffix(p, "/")
	}
	if p == "" || p == "/" {
		return ""
	}
	return p
}

var (
	jsCallAnchor = regexp.MustCompile(`this\.json|this\.raw\(|xhr\.open\(|fetch\(`)
	jsMethodRe   = regexp.MustCompile(`method:\s*["'](GET|POST|PUT|PATCH|DELETE)["']`)
	xhrOpenRe    = regexp.MustCompile(`xhr\.open\(\s*["'](GET|POST|PUT|PATCH|DELETE)["']`)
)

// extractWebChatCalls parses apps/web-chat/src/lib/api.ts for every endpoint
// the browser client hits. Two passes:
//
//	A) this.json(...) / this.raw(...) call sites — the first string literal
//	   starting with "/" is the API_BASE-relative path; the request method
//	   comes from the `method: "X"` in the same call chunk (default GET).
//	B) `${API_BASE}/...` templates (xhr.open uploads, fetchPublicShared, and
//	   the URL-builder helpers like downloadUrl/exportUrl) — method from a
//	   preceding xhr.open("X", ...) when present, else GET.
func extractWebChatCalls(t *testing.T, src string) []clientCall {
	t.Helper()
	seen := map[routeKey]bool{}
	var calls []clientCall
	add := func(method, rel, where string) {
		p := normalizeClientPath(rel)
		if p == "" {
			return
		}
		full := "/api/v1" + p
		k := routeKey{method, full}
		if seen[k] {
			return
		}
		seen[k] = true
		calls = append(calls, clientCall{method: method, path: full, where: where})
	}

	// Pass A — chunk the file at call-site anchors.
	locs := jsCallAnchor.FindAllStringIndex(src, -1)
	for n, loc := range locs {
		end := len(src)
		if n+1 < len(locs) {
			end = locs[n+1][0]
		}
		chunk := src[loc[0]:end]
		anchor := src[loc[0]:loc[1]]
		if !strings.HasPrefix(anchor, "this.") {
			continue // xhr.open / fetch are handled in pass B
		}
		// First literal in the chunk whose contents start with "/" is the path.
		rel, relEnd := "", -1
		for i := 0; i < len(chunk); i++ {
			if c := chunk[i]; c == '`' || c == '"' || c == '\'' {
				lit, litEnd := scanJSString(chunk, i)
				if strings.HasPrefix(lit, "/") {
					rel, relEnd = lit, litEnd
					break
				}
				i = litEnd
			}
		}
		if rel == "" {
			continue // e.g. this.raw(path, init, true) retry — no literal
		}
		method := "GET"
		if m := jsMethodRe.FindStringSubmatch(chunk[relEnd:]); m != nil {
			method = m[1]
		}
		add(method, rel, "api.ts "+anchor)
	}

	// Pass B — templates that embed ${API_BASE} directly.
	for i := 0; ; {
		idx := strings.Index(src[i:], "`${API_BASE}")
		if idx < 0 {
			break
		}
		at := i + idx
		lit, litEnd := scanJSString(src, at)
		i = at + (litEnd - at) + 1
		rel := strings.TrimPrefix(lit, "${API_BASE}")
		if !strings.HasPrefix(rel, "/") {
			continue // e.g. `${API_BASE}${path}` inside raw() — no static path
		}
		method := "GET"
		back := src[max(0, at-80):at]
		if m := xhrOpenRe.FindStringSubmatch(back); m != nil {
			method = m[1]
		}
		add(method, rel, "api.ts ${API_BASE} template")
	}

	sortCalls(calls)
	return calls
}

func TestWebChatClientPathsAreRegisteredRoutes(t *testing.T) {
	srcPath := repoPath("apps", "web-chat", "src", "lib", "api.ts")
	data, err := os.ReadFile(srcPath)
	if err != nil {
		t.Skipf("web-chat client source not present (%v) — this contract test requires the monorepo checkout", err)
	}

	table := buildRouteTable(t)
	calls := extractWebChatCalls(t, string(data))

	// Extraction self-check: if the parser silently regresses (api.ts is
	// rewritten in a style the extractor doesn't recognize) we must fail,
	// not pass vacuously. api.ts calls ~60 distinct (method, path) pairs.
	if len(calls) < 45 {
		t.Fatalf("web-chat extraction found only %d calls (expected >= 45) — extractor no longer understands api.ts's call style; update extractWebChatCalls", len(calls))
	}

	// Spot-check: the direct-dispatch media proxy paths MUST be among the
	// extracted client calls — the routes exist (router.go MediaProxy
	// block) and this pins both sides. /jobs/{id}/output is the streamed
	// artifact playback path added in Phase 11 A3.
	mustExtract := []routeKey{
		{"POST", "/api/v1/videos/generations"},
		{"POST", "/api/v1/audio/generations"},
		{"GET", "/api/v1/jobs/{param}"},
		{"POST", "/api/v1/jobs/{param}/cancel"},
		{"GET", "/api/v1/jobs/{param}/output"},
		{"POST", "/api/v1/conversations/{param}/messages"},
	}
	extracted := map[routeKey]bool{}
	for _, c := range calls {
		extracted[routeKey{c.method, c.path}] = true
	}
	for _, want := range mustExtract {
		if !extracted[want] {
			t.Errorf("extractor did not find expected client call %s %s in api.ts — extractor drift", want.method, want.pattern)
		}
	}

	for _, c := range calls {
		if !matchRoute(table, c.method, c.path) {
			t.Errorf("web-chat client calls %s %s (%s) but chat-edge registers no such route — client/server drift", c.method, c.path, c.where)
		}
	}
}

// ---------------------------------------------------------------------------
// Desktop client extraction (SranDesktop/src/SranDesktop.Infrastructure)
// ---------------------------------------------------------------------------

var (
	csPathLit = regexp.MustCompile(`\$?"(api/v1[^"]*)"`)
	csBrace   = regexp.MustCompile(`\{[^{}]*\}`)
)

// csMethodFor infers the HTTP method for a C# path literal from the
// statement containing it (backward to the previous ';' / '{' / '}').
func csMethodFor(src string, litStart int) string {
	stmtStart := 0
	for i := litStart - 1; i >= 0; i-- {
		if c := src[i]; c == ';' || c == '{' || c == '}' {
			stmtStart = i + 1
			break
		}
	}
	stmt := src[stmtStart:litStart]
	switch {
	case strings.Contains(stmt, "HttpMethod.Patch"):
		return "PATCH"
	case strings.Contains(stmt, "HttpMethod.Post"):
		return "POST"
	case strings.Contains(stmt, "HttpMethod.Put"):
		return "PUT"
	case strings.Contains(stmt, "HttpMethod.Delete"):
		return "DELETE"
	case strings.Contains(stmt, "PostAsJsonAsync"), strings.Contains(stmt, "PostAsync"):
		return "POST"
	case strings.Contains(stmt, "PutAsJsonAsync"), strings.Contains(stmt, "PutAsync"):
		return "PUT"
	case strings.Contains(stmt, "DeleteAsync"):
		return "DELETE"
	default:
		// GetAsync / GetStringAsync / `var url = $"api/v1/..."` builders —
		// every dynamic-URL builder in ChatEdgeApiClient.cs is a GET.
		return "GET"
	}
}

// normalizeCSPath maps a C# interpolated path literal onto the route-table
// shape: query cut at '?', `{expr}` after '/' → {param}, mid-segment
// interpolation truncates.
func normalizeCSPath(raw string) string {
	if i := strings.IndexByte(raw, '?'); i >= 0 {
		raw = raw[:i]
	}
	var b strings.Builder
	i := 0
	for i < len(raw) {
		if raw[i] == '{' {
			j := strings.IndexByte(raw[i:], '}')
			if j < 0 {
				break
			}
			if strings.HasSuffix(b.String(), "/") {
				b.WriteString("{param}")
				i += j + 1
				continue
			}
			break
		}
		b.WriteByte(raw[i])
		i++
	}
	p := "/" + strings.TrimSuffix(b.String(), "/")
	if p == "/api/v1" || p == "/" {
		return "" // bare prefix (e.g. the "api/v1/" concat in DownloadFileAsync)
	}
	return p
}

// Paths the C# grep-extraction cannot resolve statically, checked instead
// via the explicit fixture below. Keep this list justified.
var csSkip = map[string]string{
	// ExecutePlanAsync builds $"api/v1/conversations/{id}/{kind}/execute"
	// where {kind} ∈ {"excel","document"} — a variable in a STATIC route
	// segment. Covered by two explicit fixture entries.
	"/api/v1/conversations/{param}/{param}/execute": "ChatEdgeApiClient.ExecutePlanAsync — {kind} is excel|document",
}

// desktopFixtureCalls covers desktop-called endpoints whose paths are not
// extractable as single "api/v1/..." literals. Sources noted per entry —
// keep in sync with SranDesktop/src/SranDesktop.Infrastructure/Api/ChatEdgeApiClient.cs.
var desktopFixtureCalls = []clientCall{
	// ExecutePlanAsync(kind = "excel" | "document").
	{"POST", "/api/v1/conversations/{param}/excel/execute", "ChatEdgeApiClient.ExecuteExcelPlanAsync"},
	{"POST", "/api/v1/conversations/{param}/document/execute", "ChatEdgeApiClient.ExecuteDocumentPlanAsync"},
	// DownloadFileAsync normalizes the server-provided download_url to
	// "api/v1/files/{id}/download" via string concat ("api/v1/" + path).
	{"GET", "/api/v1/files/{param}/download", "ChatEdgeApiClient.DownloadFileAsync"},
	// NOTE deliberately absent: FirstRunViewModel probes {baseUrl}/api/v1/health,
	// which is an NGINX path alias (R11 B3) — chat-edge itself serves GET /health
	// at the root. The root route is pinned in TestServerPinnedRoutes.
}

func extractDesktopCalls(t *testing.T, dir string) []clientCall {
	t.Helper()
	seen := map[routeKey]bool{}
	var calls []clientCall
	err := filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".cs") {
			return err
		}
		data, rerr := os.ReadFile(path)
		if rerr != nil {
			return rerr
		}
		src := string(data)
		for _, m := range csPathLit.FindAllStringSubmatchIndex(src, -1) {
			raw := src[m[2]:m[3]]
			p := normalizeCSPath(raw)
			if p == "" {
				continue
			}
			if why, skip := csSkip[p]; skip {
				t.Logf("skipping unextractable path %s (%s)", p, why)
				continue
			}
			method := csMethodFor(src, m[0])
			k := routeKey{method, p}
			if seen[k] {
				continue
			}
			seen[k] = true
			calls = append(calls, clientCall{method: method, path: p, where: filepath.Base(path)})
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", dir, err)
	}
	calls = append(calls, desktopFixtureCalls...)
	sortCalls(calls)
	return calls
}

func TestDesktopClientPathsAreRegisteredRoutes(t *testing.T) {
	dir := repoPath("SranDesktop", "src", "SranDesktop.Infrastructure")
	if _, err := os.Stat(dir); err != nil {
		t.Skipf("desktop client source not present (%v) — this contract test requires the monorepo checkout", err)
	}

	table := buildRouteTable(t)
	calls := extractDesktopCalls(t, dir)

	// Extraction self-check (ChatEdgeApiClient.cs hits ~40 distinct pairs).
	if len(calls) < 30 {
		t.Fatalf("desktop extraction found only %d calls (expected >= 30) — extractor no longer understands the C# client's style; update extractDesktopCalls", len(calls))
	}

	for _, c := range calls {
		if !matchRoute(table, c.method, c.path) {
			t.Errorf("desktop client calls %s %s (%s) but chat-edge registers no such route — client/server drift", c.method, c.path, c.where)
		}
	}
}

// ---------------------------------------------------------------------------
// Server-side pins
// ---------------------------------------------------------------------------

// TestServerPinnedRoutes pins routes whose EXISTENCE clients historically
// second-guessed:
//
//   - The media-proxy block (router.go, MediaProxy != nil): web-chat's media
//     forms submit/poll/cancel/play through these; several desktop calls also
//     wrap them in tolerant 404/503 handling from the era when routes were
//     unstable. If any of these is removed, THIS test names the client
//     fallout directly.
//   - GET /health at the ROOT (not /api/v1/health): the desktop first-run
//     probe uses an nginx alias /api/v1/health; the underlying chat-edge
//     route it maps to is this one.
func TestServerPinnedRoutes(t *testing.T) {
	table := buildRouteTable(t)
	pinned := []routeKey{
		{"GET", "/health"},
		{"POST", "/api/v1/videos/generations"},
		{"POST", "/api/v1/audio/generations"},
		{"GET", "/api/v1/jobs/{param}"},
		{"POST", "/api/v1/jobs/{param}/cancel"},
		{"GET", "/api/v1/jobs/{param}/output"},
		{"GET", "/api/v1/usage/me"},
		{"POST", "/api/v1/conversations/import"},
		{"POST", "/api/v1/conversations/{param}/messages/import"},
	}
	for _, rk := range pinned {
		if !table[rk] {
			t.Errorf("pinned route %s %s is no longer registered — web-chat and/or desktop clients call it (see comment above)", rk.method, rk.pattern)
		}
	}
}
