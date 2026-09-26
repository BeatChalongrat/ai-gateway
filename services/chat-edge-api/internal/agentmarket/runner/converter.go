package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"
)

// Converter calls the doc-converter service (services/doc-converter): a real
// LibreOffice conversion of a finished .docx/.xlsx/.pptx into a PDF (Thai
// shaped by HarfBuzz with the image's fonts, which ReportLab never did), a
// recalculated .xlsx (openpyxl writes formulas but never their values), and
// the PDF check a gate uses (page count, pdftotext text layer, page renders).
// It sits on an internal network that only the runner shares.
type Converter struct {
	BaseURL string // e.g. http://doc-converter:8096; empty means no converter
	HTTP    *http.Client
	Timeout time.Duration // per call; 0 means defaultConvertTimeout
}

const (
	defaultConvertTimeout = 90 * time.Second // the service's own limit is 60 s, plus queueing
	maxConvertedBytes     = 128 << 20
	maxCheckBytes         = 64 << 20
)

// ErrNoConverter means no converter is configured (AGENT_MARKET_CONVERTER_URL
// empty): the caller delivers without the PDF instead of failing the run.
var ErrNoConverter = errors.New("no document converter configured")

// ErrConvertTimeout is a conversion past its wall-clock limit — the
// service's (504: it killed soffice) or this client's own Timeout.
var ErrConvertTimeout = errors.New("document conversion timed out")

// ErrConvertTooLarge is a document above the service's input limit (413).
var ErrConvertTooLarge = errors.New("document too large to convert")

// ErrConverterBusy means the service is still converting something else (503).
var ErrConverterBusy = errors.New("document converter busy")

// ConvertError is a non-200 answer: the status and the service's short reason.
// errors.Is maps 504, 413 and 503 onto the sentinels above.
type ConvertError struct {
	Status int
	Reason string
}

func (e *ConvertError) Error() string {
	return fmt.Sprintf("converter %d: %s", e.Status, e.Reason)
}

func (e *ConvertError) Unwrap() error {
	switch e.Status {
	case http.StatusGatewayTimeout:
		return ErrConvertTimeout
	case http.StatusRequestEntityTooLarge:
		return ErrConvertTooLarge
	case http.StatusServiceUnavailable:
		return ErrConverterBusy
	}
	return nil
}

// Available reports whether a converter is configured.
func (c *Converter) Available() bool {
	return c != nil && strings.TrimSpace(c.BaseURL) != ""
}

// Convert sends one document and returns the converted bytes. name's
// extension picks LibreOffice's filter; to is "pdf" (default) or "xlsx"
// (recalculate a workbook). The answer is checked to be the format asked for.
func (c *Converter) Convert(ctx context.Context, name string, data []byte, to string) ([]byte, error) {
	if !c.Available() {
		return nil, ErrNoConverter
	}
	if to == "" {
		to = "pdf"
	}
	hdr := http.Header{"Content-Type": {"application/octet-stream"}, "X-Filename": {headerFileName(name)}}
	out, err := c.post(ctx, "/convert?to="+url.QueryEscape(to), data, hdr, maxConvertedBytes)
	if err != nil {
		return nil, err
	}
	switch to {
	case "pdf":
		if !bytes.HasPrefix(out, []byte("%PDF-")) {
			return nil, fmt.Errorf("converter returned something that is not a PDF (%d bytes)", len(out))
		}
	case "xlsx":
		if !bytes.HasPrefix(out, []byte("PK\x03\x04")) {
			return nil, fmt.Errorf("converter returned something that is not an xlsx (%d bytes)", len(out))
		}
	}
	return out, nil
}

// CheckResult is what the service reads from a PDF: the page count, the
// pdftotext -layout text layer, and each rendered page (72 DPI PNG) size.
type CheckResult struct {
	Pages         int           `json:"pages"`
	Text          string        `json:"text"`
	RenderedPages int           `json:"rendered_pages"`
	PagePNGSizes  []PagePNGSize `json:"page_png_sizes"`
	// RawText is pdftotext -raw: content-stream order, so a table cell's
	// lines stay together (AM2 Thai gate). PageInk is each page's share of
	// non-white pixels (a blank page is 0).
	RawText string    `json:"raw_text"`
	PageInk []float64 `json:"page_ink"`
	// FirstPNG is page 1 as a PNG (CheckWithFirstPNG only).
	FirstPNG []byte `json:"first_png_base64,omitempty"`
}

// PagePNGSize is one rendered page: pixels at 72 DPI and the PNG's size.
type PagePNGSize struct {
	Width  int `json:"width"`
	Height int `json:"height"`
	Bytes  int `json:"bytes"`
}

// Check reads a PDF's page count, text layer and page renders.
func (c *Converter) Check(ctx context.Context, pdf []byte) (CheckResult, error) {
	return c.check(ctx, pdf, false)
}

// CheckWithFirstPNG is Check plus page 1 rendered as a PNG.
func (c *Converter) CheckWithFirstPNG(ctx context.Context, pdf []byte) (CheckResult, error) {
	return c.check(ctx, pdf, true)
}

func (c *Converter) check(ctx context.Context, pdf []byte, firstPNG bool) (CheckResult, error) {
	if !c.Available() {
		return CheckResult{}, ErrNoConverter
	}
	p := "/check"
	if firstPNG {
		p += "?first_png=1"
	}
	raw, err := c.post(ctx, p, pdf, http.Header{"Content-Type": {"application/pdf"}}, maxCheckBytes)
	if err != nil {
		return CheckResult{}, err
	}
	var res CheckResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return CheckResult{}, fmt.Errorf("bad converter response: %v", err)
	}
	if res.Pages < 0 || len(res.PagePNGSizes) > res.Pages {
		return CheckResult{}, fmt.Errorf("bad converter response: %d pages, %d renders", res.Pages, len(res.PagePNGSizes))
	}
	return res, nil
}

func (c *Converter) post(ctx context.Context, pathQuery string, body []byte, hdr http.Header, limit int64) ([]byte, error) {
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = defaultConvertTimeout
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(cctx, http.MethodPost, strings.TrimRight(c.BaseURL, "/")+pathQuery, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header = hdr
	client := c.HTTP
	if client == nil {
		client = http.DefaultClient
	}
	resp, err := client.Do(req)
	if err != nil {
		// Our own deadline (not the caller's cancellation) is a timeout.
		if ctx.Err() == nil && errors.Is(cctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("%w after %s", ErrConvertTimeout, timeout)
		}
		return nil, fmt.Errorf("converter: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
		var e struct {
			Error string `json:"error"`
		}
		reason := strings.TrimSpace(string(raw))
		if json.Unmarshal(raw, &e) == nil && e.Error != "" {
			reason = e.Error
		}
		return nil, &ConvertError{Status: resp.StatusCode, Reason: trim(reason, 200)}
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil {
		if ctx.Err() == nil && errors.Is(cctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("%w after %s", ErrConvertTimeout, timeout)
		}
		return nil, fmt.Errorf("converter: %w", err)
	}
	if int64(len(raw)) > limit {
		return nil, fmt.Errorf("converter answer larger than %d bytes", limit)
	}
	return raw, nil
}

// headerFileName is the base name the service reads the extension from. A
// name that is not plain printable ASCII (a Thai file name) is sent as
// "document" + its extension: only the extension matters to the service.
func headerFileName(name string) string {
	base := path.Base(strings.ReplaceAll(name, "\\", "/"))
	for _, r := range base {
		if r < 0x20 || r > 0x7e {
			return "document" + strings.ToLower(path.Ext(base))
		}
	}
	return base
}
