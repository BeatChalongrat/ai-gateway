package service

// web_fetch tests (Phase 3).
//
// The SSRF surface is the point of these tests: every blocked range,
// the dial-time guard wiring (which is what makes redirects and DNS
// rebinding safe), scheme and content-type rejection, size caps, and
// the executor's user-facing behavior. Success paths use httptest
// servers on 127.0.0.1, so they construct the fetcher with a
// permissive guard; the guard itself is tested separately.

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// allowLoopback lets tests talk to httptest servers.
func allowLoopback(_, _ string) error { return nil }

func TestBlockedIPRange(t *testing.T) {
	blocked := []string{
		"127.0.0.1", "127.8.9.1", // loopback
		"10.0.0.5", "172.16.1.1", "192.168.1.1", // RFC1918
		"169.254.169.254", "169.254.1.1", // link-local + cloud metadata
		"100.64.0.1", "100.127.255.254", // CGNAT
		"0.0.0.0", "224.0.0.1", // unspecified + multicast
		"::1", "fe80::1", "fc00::1", "fd12:3456::1", "::", // v6 loopback/link-local/ULA/unspecified
	}
	for _, s := range blocked {
		ip := net.ParseIP(s)
		if ip == nil {
			t.Fatalf("bad test IP %q", s)
		}
		if err := blockedIPRange(ip); err == nil {
			t.Errorf("blockedIPRange(%s) = nil, want blocked", s)
		}
	}
	allowed := []string{"93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "100.128.0.1"}
	for _, s := range allowed {
		if err := blockedIPRange(net.ParseIP(s)); err != nil {
			t.Errorf("blockedIPRange(%s) = %v, want nil (public)", s, err)
		}
	}
}

func TestDefaultDialGuard(t *testing.T) {
	for _, addr := range []string{"127.0.0.1:80", "[::1]:443", "10.1.2.3:8080", "169.254.169.254:80"} {
		if err := defaultDialGuard("tcp", addr); err == nil {
			t.Errorf("defaultDialGuard(%s) = nil, want blocked", addr)
		}
	}
	if err := defaultDialGuard("tcp", "93.184.216.34:443"); err != nil {
		t.Errorf("public dial blocked: %v", err)
	}
	// A hostname reaching the dialer (should never happen — transport
	// dials resolved IPs) is refused, not trusted.
	if err := defaultDialGuard("tcp", "evil.example:80"); err == nil {
		t.Error("non-IP dial target must be refused")
	}
}

func TestFetch_RejectsBlockedTargetsBeforeDial(t *testing.T) {
	f := NewWebFetcher() // real guard
	ctx := context.Background()
	cases := []string{
		"http://localhost/admin",
		"http://127.0.0.1:8080/",
		"http://169.254.169.254/latest/meta-data/",
		"http://10.0.0.5/internal",
		"http://[::1]/",
	}
	for _, u := range cases {
		if _, err := f.Fetch(ctx, u, 0); err == nil {
			t.Errorf("Fetch(%s) succeeded, want SSRF block", u)
		}
	}
}

func TestFetch_RejectsNonHTTPSchemes(t *testing.T) {
	f := NewWebFetcher()
	for _, u := range []string{"ftp://example.com/x", "file:///etc/passwd", "gopher://example.com"} {
		if _, err := f.Fetch(context.Background(), u, 0); err == nil {
			t.Errorf("Fetch(%s) succeeded, want scheme rejection", u)
		}
	}
}

func TestFetch_SuccessExtractsTitleAndText(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, `<html><head><title>ราคาสินค้า Q3</title><script>evil()</script></head>`+
			`<body><style>.x{}</style><h1>Quarterly prices</h1><p>Widget: 100 บาท</p></body></html>`)
	}))
	defer srv.Close()

	f := newWebFetcherWithGuard(allowLoopback, webFetchMaxBodyBytes)
	res, err := f.Fetch(context.Background(), srv.URL, 0)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if res.Title != "ราคาสินค้า Q3" {
		t.Errorf("title = %q", res.Title)
	}
	if !strings.Contains(res.Text, "Quarterly prices") || !strings.Contains(res.Text, "100 บาท") {
		t.Errorf("text missing content: %q", res.Text)
	}
	if strings.Contains(res.Text, "evil()") || strings.Contains(res.Text, ".x{}") {
		t.Errorf("script/style leaked into text: %q", res.Text)
	}
	if res.Truncated {
		t.Error("small page reported truncated")
	}
}

func TestFetch_RejectsDisallowedContentType(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write([]byte{0x89, 0x50, 0x4E, 0x47})
	}))
	defer srv.Close()
	f := newWebFetcherWithGuard(allowLoopback, webFetchMaxBodyBytes)
	if _, err := f.Fetch(context.Background(), srv.URL, 0); err == nil ||
		!strings.Contains(err.Error(), "content type") {
		t.Fatalf("want content-type rejection, got %v", err)
	}
}

func TestFetch_TruncatesOversizeBody(t *testing.T) {
	big := strings.Repeat("A", 4096)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		fmt.Fprint(w, big)
	}))
	defer srv.Close()
	f := newWebFetcherWithGuard(allowLoopback, 1024) // 1 KiB cap for the test
	res, err := f.Fetch(context.Background(), srv.URL, 0)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if !res.Truncated {
		t.Error("oversize body not flagged truncated")
	}
	if len(res.Text) > 1024 {
		t.Errorf("body cap not applied: %d bytes", len(res.Text))
	}
}

func TestFetch_MaxCharsClamp(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		fmt.Fprint(w, strings.Repeat("บ", 3000))
	}))
	defer srv.Close()
	f := newWebFetcherWithGuard(allowLoopback, webFetchMaxBodyBytes)
	// max_length below the floor clamps UP to webFetchMinChars.
	res, err := f.Fetch(context.Background(), srv.URL, 10)
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	if got := len([]rune(res.Text)); got > webFetchMinChars+1 { // +1 for the … marker
		t.Errorf("char clamp: got %d runes", got)
	}
	if !res.Truncated {
		t.Error("char-capped result not flagged truncated")
	}
}

func TestFetch_RedirectToGuardedAddressIsBlocked(t *testing.T) {
	// Target server the redirect points at — its port is denied by the
	// test guard, standing in for "redirect into the internal network".
	inner := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		fmt.Fprint(w, "secret internal page")
	}))
	defer inner.Close()
	outer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, inner.URL, http.StatusFound)
	}))
	defer outer.Close()

	_, innerPort, _ := net.SplitHostPort(strings.TrimPrefix(inner.URL, "http://"))
	guard := func(_, address string) error {
		if _, port, _ := net.SplitHostPort(address); port == innerPort {
			return fmt.Errorf("blocked address: guarded port %s", port)
		}
		return nil
	}
	f := newWebFetcherWithGuard(guard, webFetchMaxBodyBytes)
	res, err := f.Fetch(context.Background(), outer.URL, 0)
	if err == nil {
		t.Fatalf("redirect into guarded address succeeded: %+v", res)
	}
	if !strings.Contains(err.Error(), "blocked address") {
		t.Errorf("unexpected error flavor: %v", err)
	}
}

func TestFetch_RedirectLimit(t *testing.T) {
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, srv.URL+r.URL.Path+"x", http.StatusFound) // endless chain
	}))
	defer srv.Close()
	f := newWebFetcherWithGuard(allowLoopback, webFetchMaxBodyBytes)
	if _, err := f.Fetch(context.Background(), srv.URL, 0); err == nil ||
		!strings.Contains(err.Error(), "redirect") {
		t.Fatalf("want redirect-limit error, got %v", err)
	}
}

// ── executor integration ─────────────────────────────────────────────

type stubFetcher struct {
	res *WebFetchResult
	err error
}

func (s stubFetcher) Fetch(context.Context, string, int) (*WebFetchResult, error) {
	return s.res, s.err
}

func TestExecWebFetch_FormatsResult(t *testing.T) {
	e := &ToolExecutor{}
	e.SetWebFetch(stubFetcher{res: &WebFetchResult{
		Title: "Example", FinalURL: "https://example.com/", Text: "hello world",
	}}, nil)
	out := e.Execute(context.Background(), GatewayToolCall{
		Function: GatewayToolCallFunction{Name: "web_fetch", Arguments: `{"url":"https://example.com/"}`},
	}, ToolExecContext{})
	for _, want := range []string{"Title: Example", "URL: https://example.com/", "hello world"} {
		if !strings.Contains(out, want) {
			t.Errorf("result missing %q:\n%s", want, out)
		}
	}
}

func TestExecWebFetch_SSRFBlockHasSafeUserMessage(t *testing.T) {
	e := &ToolExecutor{}
	e.SetWebFetch(stubFetcher{err: fmt.Errorf("blocked address 127.0.0.1: loopback address")}, nil)
	out := e.Execute(context.Background(), GatewayToolCall{
		Function: GatewayToolCallFunction{Name: "web_fetch", Arguments: `{"url":"http://localhost/"}`},
	}, ToolExecContext{})
	if !strings.Contains(out, "private or internal address") {
		t.Errorf("SSRF block should return the safe explanation, got:\n%s", out)
	}
}

func TestToolDefs_IncludesWebFetchWhenWired(t *testing.T) {
	e := &ToolExecutor{}
	e.SetWebFetch(stubFetcher{}, nil)
	defs := e.ToolDefs(nil)
	found := false
	for _, d := range defs {
		if d.Function.Name == "web_fetch" {
			found = true
		}
	}
	if !found {
		t.Error("web_fetch missing from ToolDefs when wired")
	}
	if len((&ToolExecutor{}).ToolDefs(nil)) != 0 {
		t.Error("unwired executor should offer no tools")
	}
}

func TestCapabilityStatement_WebFetchRemovesURLDenial(t *testing.T) {
	pc := &PromptCompiler{}
	got := pc.capabilityStatement(CompileContext{ActiveTools: []string{"web_search", "web_fetch"}})
	cannot := got[strings.Index(got, "You CANNOT"):]
	for _, banned := range []string{"open or fetch a specific URL", "browse the internet or open URLs"} {
		if strings.Contains(cannot, banned) {
			t.Errorf("URL denial %q survives with web_fetch active:\n%s", banned, got)
		}
	}
	if !strings.Contains(got, "web_fetch") {
		t.Errorf("CAN list should mention web_fetch:\n%s", got)
	}
	// fetch-only: can read URLs, cannot search.
	got = pc.capabilityStatement(CompileContext{ActiveTools: []string{"web_fetch"}})
	if !strings.Contains(got, "you can only read pages") {
		t.Errorf("fetch-only statement should deny searching:\n%s", got)
	}
}
