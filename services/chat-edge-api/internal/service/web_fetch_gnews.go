package service

// FIX126 U1 — web_fetch turns Google News redirect links into the
// publisher's article URL.
//
// With no web_search provider configured the model reads Google News
// RSS feeds through web_fetch and copies each item's <link>, which is a
// news.google.com/rss/articles/… redirect, not the article. This file is
// the Go port of backend/app/services/search/url_resolve.py:
//
//   - OLD-format ids (CBMi… whose protobuf carries the URL) decode with
//     no network;
//   - NEW-format ids (the protobuf holds an opaque AU_yqL… token) are
//     resolved the way the news.google.com page does it: GET the article
//     page for its signature + timestamp, then POST batchexecute
//     (Fbv4je / garturlreq). Both calls go through the web_fetch client,
//     so the same dial-time SSRF guard applies.
//
// The network half is bounded so it never holds a fetch up: a per-call
// timeout, at most gnewsMaxConcurrency resolutions in flight per
// fetcher, a wall-clock budget per fetch (a link not resolved by then
// keeps its fallback) and an in-process cache of hits and misses. Any
// failure keeps the item's <source url> site, else the redirect; a
// resolve failure never fails the fetch.
//
// Kill switch: WEB_FETCH_RESOLVE_GOOGLE_NEWS=0 (default on).

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	gnewsHost           = "news.google.com"
	gnewsCallTimeout    = 2500 * time.Millisecond
	gnewsBudget         = 3 * time.Second
	gnewsMaxConcurrency = 4
	gnewsCacheMax       = 2048
	gnewsCacheTTLHit    = 7 * 24 * time.Hour
	gnewsCacheTTLMiss   = 10 * time.Minute
	gnewsPageMaxBytes   = int64(1 << 20)
	gnewsBatchURL       = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
	gnewsBrowserUA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
		"(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

// gnewsEnabled reads the kill switch on every call so an operator (or a
// test) can flip it without a restart.
func gnewsEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("WEB_FETCH_RESOLVE_GOOGLE_NEWS"))) {
	case "0", "false", "no", "off":
		return false
	}
	return true
}

// /rss/articles/<id>, /articles/<id>, /read/<id> — the id is the last
// path segment in every variant Google serves.
var gnewsPathRe = regexp.MustCompile(`^/(?:rss/)?(?:articles|read)/([A-Za-z0-9_-]+)`)

// googleNewsID is the article id of a Google News redirect link, else "".
func googleNewsID(raw string) string {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || !strings.EqualFold(u.Hostname(), gnewsHost) {
		return ""
	}
	if m := gnewsPathRe.FindStringSubmatch(u.EscapedPath()); len(m) == 2 {
		return m[1]
	}
	return ""
}

// isPublisherURL: an absolute http(s) URL that is not Google News itself.
func isPublisherURL(s string) bool {
	u, err := url.Parse(s)
	if err != nil {
		return false
	}
	return (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != "" &&
		!strings.EqualFold(u.Hostname(), gnewsHost)
}

var gnewsDecodedURLRe = regexp.MustCompile(`^https?://[^\s\x00-\x1f"<>]+`)

// decodeGoogleNewsOffline decodes an OLD-format id to the article URL
// with no network. The id is URL-safe base64 of
// `08 13 22 <varint len> <url> …`; a new-format id decodes to AU_yqL…
// instead of a URL and yields "".
func decodeGoogleNewsOffline(articleID string) string {
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(articleID, "="))
	if err != nil {
		return ""
	}
	body := raw
	if prefix := []byte{0x08, 0x13, 0x22}; bytes.HasPrefix(raw, prefix) {
		rest := raw[len(prefix):]
		length, shift, i := 0, 0, 0
		for i < len(rest) && i < 5 {
			b := rest[i]
			length |= int(b&0x7f) << shift
			i++
			if b&0x80 == 0 {
				break
			}
			shift += 7
		}
		body = rest[i:]
		if length > 0 && length < len(body) {
			body = body[:length]
		}
	}
	if !utf8.Valid(body) {
		return ""
	}
	text := string(body)
	if strings.HasPrefix(text, "AU_yqL") {
		return ""
	}
	if m := gnewsDecodedURLRe.FindString(text); m != "" && isPublisherURL(m) {
		return m
	}
	return ""
}

// parseGoogleNewsBatch pulls the article URL out of a garturlreq
// batchexecute reply: `)]}'`, a blank line, then the envelope array.
func parseGoogleNewsBatch(text string) string {
	parts := strings.SplitN(text, "\n\n", 2)
	if len(parts) != 2 {
		return ""
	}
	chunk := strings.TrimSpace(parts[1])
	var envelopes []json.RawMessage
	dec := json.NewDecoder(strings.NewReader(chunk))
	if err := dec.Decode(&envelopes); err != nil {
		return ""
	}
	for _, rawEnv := range envelopes {
		var env []any
		if json.Unmarshal(rawEnv, &env) != nil || len(env) < 3 {
			continue
		}
		tag, _ := env[0].(string)
		payload, _ := env[2].(string)
		if tag != "wrb.fr" || payload == "" {
			continue
		}
		var inner []any
		if json.Unmarshal([]byte(payload), &inner) != nil || len(inner) < 2 {
			continue
		}
		if s, ok := inner[1].(string); ok && isPublisherURL(s) {
			return s
		}
		return ""
	}
	return ""
}

type gnewsCacheEntry struct {
	expires time.Time
	value   string // "" = remembered miss
}

// googleNewsResolver is owned by one WebFetcher (a process singleton in
// production) and shares its SSRF-guarded client.
type googleNewsResolver struct {
	client      *http.Client
	callTimeout time.Duration
	budget      time.Duration
	sem         chan struct{}
	now         func() time.Time

	mu    sync.Mutex
	cache map[string]gnewsCacheEntry
}

func newGoogleNewsResolver(client *http.Client) *googleNewsResolver {
	return &googleNewsResolver{
		client:      client,
		callTimeout: gnewsCallTimeout,
		budget:      gnewsBudget,
		sem:         make(chan struct{}, gnewsMaxConcurrency),
		now:         time.Now,
		cache:       make(map[string]gnewsCacheEntry),
	}
}

func (r *googleNewsResolver) cacheGet(key string) (string, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	e, ok := r.cache[key]
	if !ok {
		return "", false
	}
	if r.now().After(e.expires) {
		delete(r.cache, key)
		return "", false
	}
	return e.value, true
}

func (r *googleNewsResolver) cachePut(key, value string) {
	ttl := gnewsCacheTTLMiss
	if value != "" {
		ttl = gnewsCacheTTLHit
	}
	now := r.now()
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.cache) >= gnewsCacheMax {
		for k, e := range r.cache {
			if now.After(e.expires) {
				delete(r.cache, k)
			}
		}
		for k := range r.cache {
			if len(r.cache) < gnewsCacheMax {
				break
			}
			delete(r.cache, k)
		}
	}
	r.cache[key] = gnewsCacheEntry{expires: now.Add(ttl), value: value}
}

// resolve returns the publisher URL for a Google News redirect, or ""
// when it cannot be had (not a redirect, failure, timeout, budget).
func (r *googleNewsResolver) resolve(ctx context.Context, link string) string {
	id := googleNewsID(link)
	if id == "" {
		return ""
	}
	key := id // ?oc=5 and friends do not change the article
	if v, ok := r.cacheGet(key); ok {
		return v
	}
	if decoded := decodeGoogleNewsOffline(id); decoded != "" {
		r.cachePut(key, decoded)
		return decoded
	}
	select {
	case r.sem <- struct{}{}:
	case <-ctx.Done():
		return "" // budget spent waiting for a slot: not a miss, don't cache
	}
	resolved, err := r.resolveOnline(ctx, id)
	<-r.sem
	if err != nil {
		if ctx.Err() != nil {
			// The fetch's budget (or the request) ended it — not Google's
			// answer, so the next fetch may try again.
			return ""
		}
		slog.Info("web_fetch_gnews_resolve_failed", "error", err.Error())
	}
	r.cachePut(key, resolved)
	return resolved
}

var (
	gnewsSigRe = regexp.MustCompile(`data-n-a-sg="([^"]+)"`)
	gnewsTsRe  = regexp.MustCompile(`data-n-a-ts="([^"]+)"`)
)

func (r *googleNewsResolver) call(ctx context.Context, req *http.Request) (string, error) {
	cctx, cancel := context.WithTimeout(ctx, r.callTimeout)
	defer cancel()
	resp, err := r.client.Do(req.WithContext(cctx))
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, gnewsPageMaxBytes))
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// resolveOnline maps a new-format id through Google's own two calls.
// What leaves is Google's article id — no user content.
func (r *googleNewsResolver) resolveOnline(ctx context.Context, id string) (string, error) {
	getReq, err := http.NewRequestWithContext(ctx, http.MethodGet,
		"https://"+gnewsHost+"/rss/articles/"+id, nil)
	if err != nil {
		return "", err
	}
	getReq.Header.Set("User-Agent", gnewsBrowserUA)
	page, err := r.call(ctx, getReq)
	if err != nil {
		return "", fmt.Errorf("article page: %w", err)
	}
	sg := gnewsSigRe.FindStringSubmatch(page)
	ts := gnewsTsRe.FindStringSubmatch(page)
	if len(sg) != 2 || len(ts) != 2 {
		return "", fmt.Errorf("article page has no signature")
	}
	signature, timestamp := html.UnescapeString(sg[1]), html.UnescapeString(ts[1])
	if _, err := strconv.ParseUint(timestamp, 10, 64); err != nil {
		return "", fmt.Errorf("article page timestamp not numeric")
	}
	idJSON, _ := json.Marshal(id)
	sigJSON, _ := json.Marshal(signature)
	inner := `["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,` +
		`null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],` +
		string(idJSON) + "," + timestamp + "," + string(sigJSON) + "]"
	freq, _ := json.Marshal([][][]string{{{"Fbv4je", inner}}})
	body := "f.req=" + url.QueryEscape(string(freq))
	postReq, err := http.NewRequestWithContext(ctx, http.MethodPost, gnewsBatchURL, strings.NewReader(body))
	if err != nil {
		return "", err
	}
	postReq.Header.Set("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
	postReq.Header.Set("User-Agent", gnewsBrowserUA)
	reply, err := r.call(ctx, postReq)
	if err != nil {
		return "", fmt.Errorf("batchexecute: %w", err)
	}
	if u := parseGoogleNewsBatch(reply); u != "" {
		return u, nil
	}
	return "", fmt.Errorf("batchexecute reply has no article URL")
}

// resolveAll resolves links concurrently within one budget. The map
// holds only the links that resolved in time.
func (r *googleNewsResolver) resolveAll(ctx context.Context, links []string) map[string]string {
	out := make(map[string]string, len(links))
	if len(links) == 0 {
		return out
	}
	bctx, cancel := context.WithTimeout(ctx, r.budget)
	defer cancel()
	var mu sync.Mutex
	var wg sync.WaitGroup
	for _, l := range links {
		wg.Add(1)
		go func(link string) {
			defer wg.Done()
			if v := r.resolve(bctx, link); v != "" {
				mu.Lock()
				out[link] = v
				mu.Unlock()
			}
		}(l)
	}
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-bctx.Done():
		// Every in-flight call is bound to bctx and returns promptly;
		// wait so no goroutine writes after we return.
		<-done
	}
	mu.Lock()
	defer mu.Unlock()
	res := make(map[string]string, len(out))
	for k, v := range out {
		res[k] = v
	}
	return res
}

// ---------------------------------------------------------------------------
// Feed rewriting
// ---------------------------------------------------------------------------

var (
	feedItemRe     = regexp.MustCompile(`(?is)<item\b[^>]*>.*?</item\s*>|<entry\b[^>]*>.*?</entry\s*>`)
	feedRSSLinkRe  = regexp.MustCompile(`(?is)<link\s*>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</link\s*>`)
	feedAtomLinkRe = regexp.MustCompile(`(?is)<link\b[^>]*?\bhref\s*=\s*"([^"]*)"`)
	feedSourceRe   = regexp.MustCompile(`(?is)<source\b[^>]*?\burl\s*=\s*"([^"]*)"`)
	feedMarkerRe   = regexp.MustCompile(`(?is)<(?:rss|feed|rdf:RDF)\b`)
)

// isGoogleNewsFeed: a news.google.com/rss/… document, or any RSS/Atom
// body whose item links are Google News redirects.
func isGoogleNewsFeed(finalURL, body string) bool {
	if u, err := url.Parse(finalURL); err == nil &&
		strings.EqualFold(u.Hostname(), gnewsHost) && strings.HasPrefix(u.Path, "/rss/") &&
		googleNewsID(finalURL) == "" && feedItemRe.MatchString(body) {
		return true
	}
	return feedMarkerRe.MatchString(body) && strings.Contains(body, gnewsHost+"/")
}

func xmlAttrEscape(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch r {
		case '&':
			b.WriteString("&amp;")
		case '<':
			b.WriteString("&lt;")
		case '>':
			b.WriteString("&gt;")
		case '"':
			b.WriteString("&quot;")
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

type feedItem struct {
	start, end int
	link       string // as written in the XML (escaped)
	source     string // <source url>, unescaped; "" when absent
}

// rewriteGoogleNewsFeed replaces each item's Google News redirect with
// the publisher article, else its <source url> site, else leaves it.
// Every occurrence of the redirect inside the item (the <link> and the
// escaped href in <description>) is replaced, so the model never sees
// it next to the real URL. Returns the body and the counts.
func (r *googleNewsResolver) rewriteGoogleNewsFeed(ctx context.Context, body string) (string, int, int, int) {
	locs := feedItemRe.FindAllStringIndex(body, -1)
	items := make([]feedItem, 0, len(locs))
	var links []string
	seen := map[string]bool{}
	for _, loc := range locs {
		chunk := body[loc[0]:loc[1]]
		link := ""
		if m := feedRSSLinkRe.FindStringSubmatch(chunk); len(m) == 2 {
			link = m[1]
		} else if m := feedAtomLinkRe.FindStringSubmatch(chunk); len(m) == 2 {
			link = m[1]
		}
		plain := html.UnescapeString(link)
		if link == "" || googleNewsID(plain) == "" {
			continue
		}
		src := ""
		if m := feedSourceRe.FindStringSubmatch(chunk); len(m) == 2 {
			if s := html.UnescapeString(m[1]); isPublisherURL(s) {
				src = s
			}
		}
		items = append(items, feedItem{start: loc[0], end: loc[1], link: link, source: src})
		if !seen[plain] {
			seen[plain] = true
			links = append(links, plain)
		}
	}
	if len(items) == 0 {
		return body, 0, 0, 0
	}
	resolved := r.resolveAll(ctx, links)

	var b strings.Builder
	b.Grow(len(body))
	prev, nResolved, nSource := 0, 0, 0
	for _, it := range items {
		b.WriteString(body[prev:it.start])
		chunk := body[it.start:it.end]
		target := resolved[html.UnescapeString(it.link)]
		if target != "" {
			nResolved++
		} else if it.source != "" {
			target = it.source
			nSource++
		}
		if target != "" {
			plain := html.UnescapeString(it.link)
			esc := xmlAttrEscape(target)
			if plain == xmlAttrEscape(plain) {
				// The usual case (…?oc=5): one spelling everywhere.
				chunk = strings.ReplaceAll(chunk, plain, esc)
			} else {
				// Inside <description> the href is escaped once more:
				// replace the doubly escaped form first.
				chunk = strings.ReplaceAll(chunk, xmlAttrEscape(xmlAttrEscape(plain)), xmlAttrEscape(esc))
				chunk = strings.ReplaceAll(chunk, xmlAttrEscape(plain), esc)
			}
		}
		b.WriteString(chunk)
		prev = it.end
	}
	b.WriteString(body[prev:])
	return b.String(), len(items), nResolved, nSource
}
