package service

// FIX126 U1 — web_fetch hands the model publisher URLs, not Google News
// redirects. Recorded fixtures only: the feeds under testdata/gnews, the
// live new-format id seen on 2026-09-21 (same fixture as
// backend/tests/test_search_url_resolve.py) and the article page +
// batchexecute reply Google serves for it. The resolver's HTTP client
// is a stub transport; nothing here reaches Google or any provider.
//
// RED/GREEN: run with WEB_FETCH_RESOLVE_GOOGLE_NEWS=0 to see the
// feature off (the rewrite tests fail); unset, they pass.

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	gnNewID = "CBMiZkFVX3lxTE5IVWIyNkZCRzgzazRUNHZqN1U4OTNPeVo2dVZLM1pUSkw1Q3o0UGxYVXJsQ0tF" +
		"anlXZ2FMbGZDUDdWcmxrVEV4STFFV2pfNmd6SEY3Wl85aldUUUk4dDJRQnI2VmZidw"
	gnNewTarget  = "https://www.bangkokpost.com/business/general/3100001/fed-holds-rates"
	gnNewID2     = "CBMiJ0FVX3lxTHNlY29uZFJlY29yZGVkVG9rZW5Gb3JUZXN0c09ubHlfeNIBAA"
	gnNewTarget2 = "https://www.matichon.co.th/economy/news_5000001"

	gnOldReuters    = "https://www.reuters.com/markets/us/fed-signals-cut-2026-09-20/"
	gnOldThairath   = "https://www.thairath.co.th/money/economics/2800001"
	gnOldPrachachat = "https://www.prachachat.net/finance/news-1700001?utm_source=gn&ref=rss"
)

func gnArticlePage(id string) string {
	return `<html><body><c-wiz><div jscontroller="aLI87" data-n-a-id="` + id +
		`" data-n-a-ts="1758436800" data-n-a-sg="AZ5r3eT_recordedSignature"></div></c-wiz></body></html>`
}

func gnBatchReply(target string) string {
	return ")]}'\n\n" + `[["wrb.fr","Fbv4je","[\"garturlres\",\"` + target +
		`\",1]",null,null,null,"generic"],["di",42],["af.httprm",41,"-1234",8]]` + "\n"
}

// gnOldID builds an OLD-format id (the protobuf carries the URL).
func gnOldID(target string) string {
	raw := append([]byte{0x08, 0x13, 0x22, byte(len(target))}, target...)
	raw = append(raw, 0xd2, 0x01, 0x00)
	return base64.RawURLEncoding.EncodeToString(raw)
}

// gnStub is the resolver's HTTP transport: every Google call lands here.
type gnStub struct {
	t        *testing.T
	handle   func(req *http.Request, body string) (*http.Response, error)
	calls    atomic.Int32
	inflight atomic.Int32
	maxSeen  atomic.Int32
}

func (s *gnStub) RoundTrip(req *http.Request) (*http.Response, error) {
	s.calls.Add(1)
	n := s.inflight.Add(1)
	defer s.inflight.Add(-1)
	for {
		m := s.maxSeen.Load()
		if n <= m || s.maxSeen.CompareAndSwap(m, n) {
			break
		}
	}
	if req.URL.Hostname() != gnewsHost {
		s.t.Errorf("resolver called a non-Google host: %s", req.URL.Host)
	}
	body := ""
	if req.Body != nil {
		b, _ := io.ReadAll(req.Body)
		body = string(b)
	}
	if s.handle == nil {
		s.t.Errorf("unexpected resolver call: %s %s", req.Method, req.URL)
		return nil, errors.New("no network in tests")
	}
	return s.handle(req, body)
}

func gnResp(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     http.Header{"Content-Type": []string{"text/html; charset=utf-8"}},
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

// gnRecordedGoogle answers like news.google.com for the given id→target map.
func gnRecordedGoogle(targets map[string]string) func(*http.Request, string) (*http.Response, error) {
	return func(req *http.Request, body string) (*http.Response, error) {
		switch {
		case req.Method == http.MethodGet && strings.HasPrefix(req.URL.Path, "/rss/articles/"):
			id := strings.TrimPrefix(req.URL.Path, "/rss/articles/")
			if _, ok := targets[id]; !ok {
				return gnResp(404, "not found"), nil
			}
			return gnResp(200, gnArticlePage(id)), nil
		case req.Method == http.MethodPost && req.URL.String() == gnewsBatchURL:
			form, err := url.ParseQuery(body)
			if err != nil {
				return gnResp(400, ""), nil
			}
			freq := form.Get("f.req")
			for id, target := range targets {
				if strings.Contains(freq, id) && strings.Contains(freq, "AZ5r3eT_recordedSignature") &&
					strings.Contains(freq, "1758436800") && strings.Contains(freq, "Fbv4je") {
					return gnResp(200, gnBatchReply(target)), nil
				}
			}
			return gnResp(400, ""), nil
		}
		return gnResp(404, ""), nil
	}
}

// gnLoopbackOnly is the fetcher's own transport in these tests: it
// reaches the httptest servers and nothing else, so even with the
// feature switched off (RED run) no request leaves the machine.
type gnLoopbackOnly struct{ base http.RoundTripper }

func (l gnLoopbackOnly) RoundTrip(req *http.Request) (*http.Response, error) {
	if h := req.URL.Hostname(); h != "127.0.0.1" && h != "::1" {
		return nil, fmt.Errorf("no network in tests: %s", req.URL.Host)
	}
	return l.base.RoundTrip(req)
}

// gnTestFetcher: loopback-guard fetcher, loopback-only transport, and a
// Google resolver that talks to stub.
func gnTestFetcher(stub *gnStub) *WebFetcher {
	f := newWebFetcherWithGuard(allowLoopback, webFetchMaxBodyBytes)
	f.client.Transport = gnLoopbackOnly{base: f.client.Transport}
	f.gnews.client = &http.Client{Transport: stub}
	return f
}

func gnFixture(t *testing.T, name string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", "gnews", name))
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// gnFetcher serves feed at /rss/search on a loopback server and returns
// a fetcher whose Google resolver talks to stub.
func gnFetcher(t *testing.T, feed string, stub *gnStub) (*WebFetcher, string) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/xml; charset=utf-8")
		fmt.Fprint(w, feed)
	}))
	t.Cleanup(srv.Close)
	return gnTestFetcher(stub), srv.URL + "/rss/search?q=Fed&hl=th&gl=TH&ceid=TH:th"
}

func gnAssertNoRedirect(t *testing.T, text string) {
	t.Helper()
	if strings.Contains(text, "news.google.com/rss/articles/") {
		t.Errorf("a Google News redirect reached the model:\n%s", text)
	}
}

// ---------------------------------------------------------------------------

func TestGNews_OldFormatFeed_PublisherURLsOffline(t *testing.T) {
	stub := &gnStub{t: t} // any call fails the test: old format is offline
	f, feedURL := gnFetcher(t, gnFixture(t, "feed_old_format.xml"), stub)

	res, err := f.Fetch(context.Background(), feedURL, 0)
	if err != nil {
		t.Fatalf("fetch failed: %v", err)
	}
	for _, want := range []string{
		"<link>" + gnOldReuters + "</link>",
		"<link>" + gnOldThairath + "</link>",
		"<link>" + strings.ReplaceAll(gnOldPrachachat, "&", "&amp;") + "</link>",
		`&lt;a href="` + gnOldReuters + `"`, // the <description> href too
	} {
		if !strings.Contains(res.Text, want) {
			t.Errorf("feed text missing %q", want)
		}
	}
	gnAssertNoRedirect(t, res.Text)
	if n := stub.calls.Load(); n != 0 {
		t.Errorf("old-format links made %d network calls, want 0", n)
	}
}

func TestGNews_NewFormatFeed_ResolvedViaBatchexecute(t *testing.T) {
	stub := &gnStub{t: t, handle: gnRecordedGoogle(map[string]string{
		gnNewID: gnNewTarget, gnNewID2: gnNewTarget2,
	})}
	f, feedURL := gnFetcher(t, gnFixture(t, "feed_new_format.xml"), stub)

	res, err := f.Fetch(context.Background(), feedURL, 0)
	if err != nil {
		t.Fatalf("fetch failed: %v", err)
	}
	for _, want := range []string{"<link>" + gnNewTarget + "</link>", "<link>" + gnNewTarget2 + "</link>"} {
		if !strings.Contains(res.Text, want) {
			t.Errorf("feed text missing %q", want)
		}
	}
	gnAssertNoRedirect(t, res.Text)
	if n := stub.calls.Load(); n != 4 { // GET + POST per new-format link
		t.Errorf("resolver made %d calls, want 4", n)
	}

	// Cached: the same feed again costs no calls.
	if _, err := f.Fetch(context.Background(), feedURL, 0); err != nil {
		t.Fatal(err)
	}
	if n := stub.calls.Load(); n != 4 {
		t.Errorf("second fetch made %d more calls, want 0 (cache)", n-4)
	}
}

func TestGNews_ResolveFailure_FallsBackToSourceThenRedirect(t *testing.T) {
	cases := map[string]func(*http.Request, string) (*http.Response, error){
		"transport error": func(*http.Request, string) (*http.Response, error) {
			return nil, errors.New("connection reset")
		},
		"http 429": func(*http.Request, string) (*http.Response, error) {
			return gnResp(429, "Too Many Requests"), nil
		},
		"timeout": func(req *http.Request, _ string) (*http.Response, error) {
			<-req.Context().Done() // hangs until the per-call timeout
			return nil, req.Context().Err()
		},
		"batchexecute garbage": func(req *http.Request, _ string) (*http.Response, error) {
			if req.Method == http.MethodGet {
				return gnResp(200, gnArticlePage(gnNewID)), nil
			}
			return gnResp(200, ")]}'\n\nnot json"), nil
		},
	}
	for name, handle := range cases {
		t.Run(name, func(t *testing.T) {
			stub := &gnStub{t: t, handle: handle}
			f, feedURL := gnFetcher(t, gnFixture(t, "feed_new_format.xml"), stub)
			f.gnews.callTimeout = 150 * time.Millisecond

			res, err := f.Fetch(context.Background(), feedURL, 0)
			if err != nil {
				t.Fatalf("resolve failure failed the fetch: %v", err)
			}
			// Item 1 has <source url>: the site replaces the redirect.
			if !strings.Contains(res.Text, "<link>https://www.bangkokpost.com</link>") {
				t.Errorf("item with <source url> did not fall back to the site:\n%s", res.Text)
			}
			if strings.Contains(res.Text, gnNewID+"?oc=5") {
				t.Errorf("item with <source url> still carries its redirect")
			}
			// Item 2 has no <source>: the redirect stays.
			if !strings.Contains(res.Text, "<link>https://news.google.com/rss/articles/"+gnNewID2+"?oc=5</link>") {
				t.Errorf("item without <source> should keep its redirect:\n%s", res.Text)
			}
		})
	}
}

func TestGNews_BudgetAndConcurrencyRespected(t *testing.T) {
	if gnewsBudget > 3*time.Second || gnewsCallTimeout > 2500*time.Millisecond || gnewsMaxConcurrency > 4 {
		t.Fatalf("limits out of bounds: budget %v, call %v, concurrency %d",
			gnewsBudget, gnewsCallTimeout, gnewsMaxConcurrency)
	}
	if gnewsCacheTTLHit != 7*24*time.Hour || gnewsCacheTTLMiss != 10*time.Minute {
		t.Fatalf("cache TTLs: hit %v, miss %v", gnewsCacheTTLHit, gnewsCacheTTLMiss)
	}

	// 12 new-format items, a Google that never answers.
	var items strings.Builder
	for i := 0; i < 12; i++ {
		tok := fmt.Sprintf("AU_yqLbudgetToken%02d", i)
		raw := append([]byte{0x08, 0x13, 0x22, byte(len(tok))}, tok...)
		id := base64.RawURLEncoding.EncodeToString(append(raw, 0xd2, 0x01, 0x00))
		fmt.Fprintf(&items, `<item><title>n%d</title><link>https://news.google.com/rss/articles/%s?oc=5</link>`+
			`<source url="https://site%d.example.com">s</source></item>`, i, id, i)
	}
	feed := `<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>` + items.String() + `</channel></rss>`

	stub := &gnStub{t: t, handle: func(req *http.Request, _ string) (*http.Response, error) {
		<-req.Context().Done()
		return nil, req.Context().Err()
	}}
	f, feedURL := gnFetcher(t, feed, stub)
	f.gnews.budget = 300 * time.Millisecond // per-call timeout stays 2.5 s

	start := time.Now()
	res, err := f.Fetch(context.Background(), feedURL, 0)
	elapsed := time.Since(start)
	if err != nil {
		t.Fatalf("fetch failed: %v", err)
	}
	if elapsed > 1500*time.Millisecond {
		t.Errorf("fetch took %v with a 300ms budget", elapsed)
	}
	if m := stub.maxSeen.Load(); m > gnewsMaxConcurrency || m == 0 {
		t.Errorf("max concurrent resolver calls = %d, want 1..%d", m, gnewsMaxConcurrency)
	}
	for i := 0; i < 12; i++ {
		if !strings.Contains(res.Text, fmt.Sprintf("<link>https://site%d.example.com</link>", i)) {
			t.Errorf("item %d did not fall back to its site", i)
		}
	}
	gnAssertNoRedirect(t, res.Text)
	// Budget expiry is not Google's answer: nothing is cached as a miss.
	if n := len(f.gnews.cache); n != 0 {
		t.Errorf("budget-expired links were cached (%d entries)", n)
	}
}

func TestGNews_SingleArticleURL_FetchesPublisherPage(t *testing.T) {
	pub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprintf(w, "<html><head><title>Publisher %s</title></head><body>Article body</body></html>", r.URL.Path)
	}))
	defer pub.Close()

	t.Run("old format, offline", func(t *testing.T) {
		stub := &gnStub{t: t}
		f := gnTestFetcher(stub)
		target := pub.URL + "/old-article"
		res, err := f.Fetch(context.Background(), "https://news.google.com/rss/articles/"+gnOldID(target)+"?oc=5", 0)
		if err != nil {
			t.Fatalf("fetch failed: %v", err)
		}
		if res.FinalURL != target || res.Title != "Publisher /old-article" {
			t.Errorf("fetched %q (%q), want the publisher page %q", res.FinalURL, res.Title, target)
		}
	})
	t.Run("new format, batchexecute", func(t *testing.T) {
		target := pub.URL + "/new-article"
		stub := &gnStub{t: t, handle: gnRecordedGoogle(map[string]string{gnNewID: target})}
		f := gnTestFetcher(stub)
		res, err := f.Fetch(context.Background(), "https://news.google.com/rss/articles/"+gnNewID+"?oc=5", 0)
		if err != nil {
			t.Fatalf("fetch failed: %v", err)
		}
		if res.FinalURL != target {
			t.Errorf("fetched %q, want the publisher page %q", res.FinalURL, target)
		}
	})
	t.Run("resolved target still passes the SSRF pre-check", func(t *testing.T) {
		stub := &gnStub{t: t}
		f := NewWebFetcher() // real guard
		f.client.Transport = gnLoopbackOnly{base: f.client.Transport}
		f.gnews.client = &http.Client{Transport: stub}
		// Decodes to a loopback URL: must not be fetched as the target.
		// The original redirect is then fetched as asked, through the
		// real guard, which is not reachable here — use a cancelled ctx
		// so no socket is opened.
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if res, err := f.Fetch(ctx, "https://news.google.com/rss/articles/"+gnOldID("http://127.0.0.1/admin")+"?oc=5", 0); err == nil &&
			strings.Contains(res.FinalURL, "127.0.0.1") {
			t.Errorf("a resolved loopback target was fetched: %s", res.FinalURL)
		}
	})
}

func TestGNews_ThroughToolExecutor(t *testing.T) {
	stub := &gnStub{t: t, handle: gnRecordedGoogle(map[string]string{gnNewID: gnNewTarget, gnNewID2: gnNewTarget2})}
	f, feedURL := gnFetcher(t, gnFixture(t, "feed_new_format.xml"), stub)
	e := &ToolExecutor{}
	e.SetWebFetch(f, nil)
	out := e.Execute(context.Background(), GatewayToolCall{
		Function: GatewayToolCallFunction{Name: "web_fetch", Arguments: `{"url":"` + feedURL + `"}`},
	}, ToolExecContext{})
	if !strings.Contains(out, gnNewTarget) || !strings.Contains(out, gnNewTarget2) {
		t.Errorf("tool result lacks the publisher URLs:\n%s", out)
	}
	gnAssertNoRedirect(t, out)
}

func TestGNews_KillSwitch(t *testing.T) {
	t.Setenv("WEB_FETCH_RESOLVE_GOOGLE_NEWS", "0")
	stub := &gnStub{t: t}
	f, feedURL := gnFetcher(t, gnFixture(t, "feed_old_format.xml"), stub)
	res, err := f.Fetch(context.Background(), feedURL, 0)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(res.Text, "news.google.com/rss/articles/") {
		t.Error("kill switch off should leave the feed untouched")
	}
	if stub.calls.Load() != 0 {
		t.Error("kill switch off should make no resolver calls")
	}
}

func TestGNews_ProductionResolverSharesGuardedClient(t *testing.T) {
	f := NewWebFetcher()
	if f.gnews == nil || f.gnews.client != f.client {
		t.Fatal("the Google News resolver must use web_fetch's SSRF-guarded client")
	}
}

func TestGNews_Parts(t *testing.T) {
	if got := googleNewsID("https://news.google.com/rss/articles/" + gnNewID + "?oc=5"); got != gnNewID {
		t.Errorf("googleNewsID = %q", got)
	}
	for _, u := range []string{"https://news.google.com/rss/search?q=x", "https://example.com/rss/articles/CBMi", "not a url"} {
		if googleNewsID(u) != "" {
			t.Errorf("googleNewsID(%q) should be empty", u)
		}
	}
	if got := decodeGoogleNewsOffline(gnNewID); got != "" {
		t.Errorf("new-format id decoded offline to %q, want empty", got)
	}
	if got := decodeGoogleNewsOffline(gnOldID(gnOldReuters)); got != gnOldReuters {
		t.Errorf("old-format decode = %q", got)
	}
	if got := decodeGoogleNewsOffline(gnOldID("https://news.google.com/x")); got != "" {
		t.Errorf("an id decoding to Google itself must not count: %q", got)
	}
	if got := parseGoogleNewsBatch(gnBatchReply(gnNewTarget)); got != gnNewTarget {
		t.Errorf("parseGoogleNewsBatch = %q", got)
	}
	for _, bad := range []string{"", ")]}'\n\n[]", ")]}'\n\n[[\"wrb.fr\",\"Fbv4je\",\"[\\\"garturlres\\\",\\\"javascript:alert(1)\\\",1]\"]]"} {
		if got := parseGoogleNewsBatch(bad); got != "" {
			t.Errorf("parseGoogleNewsBatch(%q) = %q, want empty", bad, got)
		}
	}
	if !isGoogleNewsFeed("https://news.google.com/rss/search?q=x", "<rss><channel><item><link>x</link></item></channel></rss>") {
		t.Error("news.google.com/rss/ feed not detected")
	}
	if isGoogleNewsFeed("https://example.com/feed", "<rss><channel><item><link>https://example.com/a</link></item></channel></rss>") {
		t.Error("an ordinary feed was treated as Google News")
	}
	if isGoogleNewsFeed("https://example.com/page", "<html>see news.google.com/rss/articles/x</html>") {
		t.Error("an HTML page mentioning Google News was treated as a feed")
	}
}

// Miss caching: a definitive failure is remembered for 10 minutes.
func TestGNews_MissIsCached(t *testing.T) {
	var mu sync.Mutex
	stub := &gnStub{t: t, handle: func(*http.Request, string) (*http.Response, error) {
		mu.Lock()
		defer mu.Unlock()
		return gnResp(429, ""), nil
	}}
	f, feedURL := gnFetcher(t, gnFixture(t, "feed_new_format.xml"), stub)
	if _, err := f.Fetch(context.Background(), feedURL, 0); err != nil {
		t.Fatal(err)
	}
	first := stub.calls.Load()
	if _, err := f.Fetch(context.Background(), feedURL, 0); err != nil {
		t.Fatal(err)
	}
	if stub.calls.Load() != first {
		t.Errorf("a remembered miss was retried (%d → %d calls)", first, stub.calls.Load())
	}
	// Past the miss TTL it is tried again.
	now := time.Now().Add(gnewsCacheTTLMiss + time.Second)
	f.gnews.now = func() time.Time { return now }
	if _, err := f.Fetch(context.Background(), feedURL, 0); err != nil {
		t.Fatal(err)
	}
	if stub.calls.Load() == first {
		t.Error("an expired miss was not retried")
	}
}
