package service

// WebFetcher — SSRF-guarded page reader behind the web_fetch tool
// (Phase-3; mirrors the desktop's WebFetchTool guard semantics).
//
// Threat model: the URL comes from the MODEL (ultimately from user
// text), so this is a server-side request-forgery surface. Defences,
// in order:
//
//  1. Scheme allowlist — http/https only, checked on the initial URL
//     AND on every redirect hop (CheckRedirect).
//  2. Literal-IP pre-check — a URL whose host is already an IP in a
//     blocked range is rejected before any socket work.
//  3. Dial-time guard (authoritative, rebinding-safe) — the dialer's
//     Control hook receives the RESOLVED ip:port immediately before
//     connect(2). Checking there (rather than resolving separately
//     and comparing) means a DNS answer that changes between "check"
//     and "use" still cannot reach a blocked address, and every
//     redirect hop is re-checked because every hop dials through the
//     same transport.
//  4. Response caps — content-type allowlist, byte cap on the body,
//     redirect-count cap, overall timeout.
//
// The tool's DLP posture: web_fetch is classified EXTERNAL_EGRESS in
// the gateway's tool-arg policy (shared with the desktop tool of the
// same name), so arguments carrying protected data arrive as a
// ``_dlp_blocked`` marker and are refused by ToolExecutor.Execute
// before this code ever runs.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"syscall"
	"time"
)

const (
	webFetchTimeout      = 15 * time.Second
	webFetchMaxBodyBytes = int64(2 << 20) // 2 MiB raw body cap
	webFetchMaxRedirects = 5
	webFetchDefaultChars = 50_000
	webFetchMinChars     = 1_000
	webFetchMaxChars     = 200_000
	webFetchUserAgent    = "SRAN-Chat/1.0 (+web_fetch)"
)

// blockedIPRange reports why an IP must never be dialed, or nil when
// the address is publicly routable. Covers loopback, RFC1918 private,
// link-local (169.254.0.0/16 — including the cloud metadata endpoint
// 169.254.169.254 — and fe80::/10), CGNAT 100.64.0.0/10, IPv6 ULA
// fc00::/7, unspecified, and multicast.
func blockedIPRange(ip net.IP) error {
	switch {
	case ip.IsLoopback():
		return errors.New("loopback address")
	case ip.IsPrivate():
		return errors.New("private address")
	case ip.IsLinkLocalUnicast(), ip.IsLinkLocalMulticast():
		return errors.New("link-local address")
	case ip.IsUnspecified():
		return errors.New("unspecified address")
	case ip.IsMulticast():
		return errors.New("multicast address")
	}
	if v4 := ip.To4(); v4 != nil {
		// CGNAT 100.64.0.0/10 — not covered by IsPrivate.
		if v4[0] == 100 && v4[1] >= 64 && v4[1] <= 127 {
			return errors.New("carrier-grade NAT address")
		}
	} else if len(ip) == net.IPv6len && (ip[0]&0xfe) == 0xfc {
		// IPv6 unique-local fc00::/7.
		return errors.New("unique-local address")
	}
	return nil
}

// dialGuard vets the RESOLVED address about to be dialed. Split out as
// a field so tests can (a) point the success paths at 127.0.0.1 test
// servers and (b) prove that a redirect to a guarded address is
// refused mid-chain.
type dialGuard func(network, address string) error

func defaultDialGuard(_, address string) error {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf("unparseable dial address %q", address)
	}
	ip := net.ParseIP(host)
	if ip == nil {
		// The transport dials resolved IPs; a non-IP here is unexpected
		// and refused rather than trusted.
		return fmt.Errorf("refusing non-IP dial target %q", host)
	}
	if reason := blockedIPRange(ip); reason != nil {
		return fmt.Errorf("blocked address %s: %w", ip, reason)
	}
	return nil
}

type WebFetcher struct {
	client  *http.Client
	maxBody int64
	guard   dialGuard
	gnews   *googleNewsResolver
}

func NewWebFetcher() *WebFetcher {
	return newWebFetcherWithGuard(defaultDialGuard, webFetchMaxBodyBytes)
}

func newWebFetcherWithGuard(guard dialGuard, maxBody int64) *WebFetcher {
	dialer := &net.Dialer{
		Timeout: 10 * time.Second,
		Control: func(network, address string, _ syscall.RawConn) error {
			return guard(network, address)
		},
	}
	transport := &http.Transport{
		DialContext:           dialer.DialContext,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 10 * time.Second,
		MaxIdleConns:          4,
		// Proxies would bypass the dial guard (we'd dial the proxy, not
		// the target). web_fetch therefore never uses one.
		Proxy: nil,
	}
	f := &WebFetcher{
		client: &http.Client{
			Timeout:   webFetchTimeout,
			Transport: transport,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= webFetchMaxRedirects {
					return errors.New("too many redirects")
				}
				if req.URL.Scheme != "http" && req.URL.Scheme != "https" {
					return fmt.Errorf("redirect to disallowed scheme %q", req.URL.Scheme)
				}
				return nil
			},
		},
		maxBody: maxBody,
		guard:   guard,
	}
	// FIX126 U1 — Google News redirects resolve through this same
	// guarded client (same dialer, redirect policy, no proxy).
	f.gnews = newGoogleNewsResolver(f.client)
	return f
}

type WebFetchResult struct {
	Title     string
	FinalURL  string
	Text      string
	Truncated bool
}

var (
	titleRe  = regexp.MustCompile(`(?is)<title[^>]*>(.*?)</title>`)
	scriptRe = regexp.MustCompile(`(?is)<(script|style|noscript)[^>]*>.*?</(script|style|noscript)>`)
)

// Fetch retrieves rawURL and returns readable text. maxChars ≤ 0 uses
// the default; values are clamped to [webFetchMinChars, webFetchMaxChars].
func (f *WebFetcher) Fetch(ctx context.Context, rawURL string, maxChars int) (*WebFetchResult, error) {
	if maxChars <= 0 {
		maxChars = webFetchDefaultChars
	}
	if maxChars < webFetchMinChars {
		maxChars = webFetchMinChars
	}
	if maxChars > webFetchMaxChars {
		maxChars = webFetchMaxChars
	}

	u, err := f.checkURL(rawURL)
	if err != nil {
		return nil, err
	}
	// FIX126 U1 — a single news.google.com/rss/articles/… link is a
	// Google redirect: read the publisher's page instead. A link that
	// cannot be resolved is fetched as asked.
	if f.gnews != nil && gnewsEnabled() && googleNewsID(u.String()) != "" {
		if target := f.gnews.resolveAll(ctx, []string{u.String()})[u.String()]; target != "" {
			if tu, terr := f.checkURL(target); terr == nil {
				u = tu
			}
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", webFetchUserAgent)
	req.Header.Set("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9,application/json;q=0.8,*/*;q=0.1")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return nil, fmt.Errorf("page returned HTTP %d", resp.StatusCode)
	}
	ct := strings.ToLower(strings.TrimSpace(strings.Split(resp.Header.Get("Content-Type"), ";")[0]))
	if !allowedContentType(ct) {
		return nil, fmt.Errorf("unsupported content type %q — only text pages can be read", ct)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, f.maxBody+1))
	if err != nil {
		return nil, fmt.Errorf("reading page: %w", err)
	}
	truncated := int64(len(body)) > f.maxBody
	if truncated {
		body = body[:f.maxBody]
	}

	raw := string(body)
	finalURL := resp.Request.URL.String()
	isHTML := ct == "text/html" || ct == "application/xhtml+xml"
	// FIX126 U1 — a Google News feed's item links are Google redirects;
	// hand the model the publisher articles. Never fails the fetch.
	if !isHTML && f.gnews != nil && gnewsEnabled() && isGoogleNewsFeed(finalURL, raw) {
		out, items, resolved, site := f.gnews.rewriteGoogleNewsFeed(ctx, raw)
		raw = out
		slog.Info("web_fetch_gnews_feed",
			"items", items, "resolved", resolved, "source_site", site,
			"kept_redirect", items-resolved-site)
	}
	title := ""
	text := raw
	if isHTML {
		if m := titleRe.FindStringSubmatch(raw); len(m) == 2 {
			title = strings.Join(strings.Fields(m[1]), " ")
		}
		text = stripHTMLTags(scriptRe.ReplaceAllString(raw, " "))
	}
	if len([]rune(text)) > maxChars {
		text = truncateRunes(text, maxChars)
		truncated = true
	}

	return &WebFetchResult{
		Title:     title,
		FinalURL:  finalURL,
		Text:      text,
		Truncated: truncated,
	}, nil
}

// checkURL parses rawURL and applies the pre-dial checks: scheme
// allowlist, host present, literal-IP guard. Split out so a resolved
// Google News target is held to the same checks as the model's URL.
func (f *WebFetcher) checkURL(rawURL string) (*url.URL, error) {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return nil, fmt.Errorf("invalid URL: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return nil, fmt.Errorf("unsupported scheme %q — only http and https are allowed", u.Scheme)
	}
	if u.Hostname() == "" {
		return nil, errors.New("URL has no host")
	}
	// Fast-fail literal-IP targets (and obvious loopback names) before
	// any network work — via the SAME guard the dialer applies, so the
	// policy has a single source of truth (tests inject a permissive
	// guard; production uses defaultDialGuard).
	if ip := net.ParseIP(u.Hostname()); ip != nil {
		port := u.Port()
		if port == "" {
			if u.Scheme == "https" {
				port = "443"
			} else {
				port = "80"
			}
		}
		if err := f.guard("tcp", net.JoinHostPort(ip.String(), port)); err != nil {
			return nil, err
		}
	} else if strings.EqualFold(u.Hostname(), "localhost") && f.isDefaultGuard() {
		return nil, errors.New("blocked address: loopback address")
	}
	return u, nil
}

// isDefaultGuard lets the "localhost" name fast-path apply only under
// the production guard; a resolved localhost still hits the dial guard
// either way, so this is purely a nicer early error.
func (f *WebFetcher) isDefaultGuard() bool {
	return f.guard("tcp", "127.0.0.1:80") != nil
}

func allowedContentType(ct string) bool {
	switch {
	case strings.HasPrefix(ct, "text/"):
		return true
	case ct == "application/xhtml+xml",
		ct == "application/json",
		ct == "application/xml",
		ct == "application/rss+xml",
		ct == "application/atom+xml",
		ct == "": // some text pages omit the header; body cap still applies
		return true
	}
	return false
}
