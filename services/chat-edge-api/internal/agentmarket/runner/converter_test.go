package runner

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func converterServer(t *testing.T, h http.HandlerFunc) *Converter {
	s := httptest.NewServer(h)
	t.Cleanup(s.Close)
	return &Converter{BaseURL: s.URL + "/", HTTP: s.Client(), Timeout: 5 * time.Second}
}

func TestConverterNotConfigured(t *testing.T) {
	for _, c := range []*Converter{nil, {}, {BaseURL: "  "}} {
		if _, err := c.Convert(context.Background(), "r.docx", []byte("x"), "pdf"); err != ErrNoConverter {
			t.Fatalf("Convert without a URL: %v, want ErrNoConverter", err)
		}
		if _, err := c.Check(context.Background(), []byte("%PDF-")); err != ErrNoConverter {
			t.Fatalf("Check without a URL: %v, want ErrNoConverter", err)
		}
	}
}

func TestConverterConvertSendsTheFileAndReturnsThePDF(t *testing.T) {
	doc := []byte("PK\x03\x04 a docx")
	pdf := []byte("%PDF-1.7\n...")
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.Method != http.MethodPost || r.URL.Path != "/convert" || r.URL.Query().Get("to") != "pdf" {
			t.Errorf("request %s %s", r.Method, r.URL)
		}
		if got := r.Header.Get("X-Filename"); got != "r_v1.docx" {
			t.Errorf("X-Filename %q", got)
		}
		if !bytes.Equal(body, doc) {
			t.Errorf("body %q", body)
		}
		w.Header().Set("Content-Type", "application/pdf")
		_, _ = w.Write(pdf)
	})
	got, err := c.Convert(context.Background(), "/job/out/r_v1.docx", doc, "")
	if err != nil || !bytes.Equal(got, pdf) {
		t.Fatalf("Convert = %q, %v", got, err)
	}
}

func TestConverterRecalcAsksForXlsx(t *testing.T) {
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("to") != "xlsx" {
			t.Errorf("to=%q", r.URL.Query().Get("to"))
		}
		_, _ = w.Write([]byte("PK\x03\x04recalculated"))
	})
	if _, err := c.Convert(context.Background(), "t_v1.xlsx", []byte("PK\x03\x04"), "xlsx"); err != nil {
		t.Fatal(err)
	}
}

func TestConverterRefusesAnAnswerThatIsNotTheFormat(t *testing.T) {
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("<html>oops</html>")) })
	if _, err := c.Convert(context.Background(), "r.docx", []byte("PK"), "pdf"); err == nil {
		t.Fatal("an HTML answer must not pass as a PDF")
	}
}

func TestConverterMaps504ToTimeout(t *testing.T) {
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusGatewayTimeout)
		_, _ = w.Write([]byte(`{"error":"timed out after 60s"}`))
	})
	_, err := c.Convert(context.Background(), "big.docx", []byte("PK"), "pdf")
	if !errors.Is(err, ErrConvertTimeout) {
		t.Fatalf("504 must be ErrConvertTimeout, got %v", err)
	}
	var ce *ConvertError
	if !errors.As(err, &ce) || ce.Status != 504 || ce.Reason != "timed out after 60s" {
		t.Fatalf("ConvertError = %+v", ce)
	}
}

func TestConverterOwnTimeoutIsATimeout(t *testing.T) {
	release := make(chan struct{})
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-release:
		}
	})
	defer close(release)
	c.Timeout = 150 * time.Millisecond
	start := time.Now()
	_, err := c.Convert(context.Background(), "r.docx", []byte("PK"), "pdf")
	if !errors.Is(err, ErrConvertTimeout) {
		t.Fatalf("a call past Timeout must be ErrConvertTimeout, got %v", err)
	}
	if time.Since(start) > 3*time.Second {
		t.Fatalf("the call was not cut at its Timeout")
	}
	// The caller's own cancellation is not reported as a converter timeout.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.Convert(ctx, "r.docx", []byte("PK"), "pdf"); errors.Is(err, ErrConvertTimeout) || !errors.Is(err, context.Canceled) {
		t.Fatalf("a cancelled caller: %v", err)
	}
}

func TestConverterMaps413ToTooLarge(t *testing.T) {
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusRequestEntityTooLarge)
		_, _ = w.Write([]byte(`{"error":"input larger than 26214400 bytes"}`))
	})
	_, err := c.Convert(context.Background(), "huge.pptx", []byte("PK"), "pdf")
	if !errors.Is(err, ErrConvertTooLarge) || errors.Is(err, ErrConvertTimeout) {
		t.Fatalf("413 must be ErrConvertTooLarge, got %v", err)
	}
}

func TestConverterKeepsAShortReasonForOtherErrors(t *testing.T) {
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnsupportedMediaType)
		_, _ = w.Write([]byte(`{"error":"content is not a .docx file"}`))
	})
	_, err := c.Convert(context.Background(), "x.docx", []byte("nope"), "pdf")
	var ce *ConvertError
	if !errors.As(err, &ce) || ce.Status != 415 || ce.Reason != "content is not a .docx file" {
		t.Fatalf("got %v", err)
	}
	if errors.Is(err, ErrConvertTimeout) || errors.Is(err, ErrConvertTooLarge) || errors.Is(err, ErrConverterBusy) {
		t.Fatalf("415 matched a sentinel: %v", err)
	}
}

func TestConverterCheckDecodesTheResult(t *testing.T) {
	png := []byte("\x89PNG\r\n\x1a\nfake")
	c := converterServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/check" || r.URL.Query().Get("first_png") != "1" {
			t.Errorf("request %s", r.URL)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"pages":1,"text":"สรุปใบแจ้งหนี้ของ บริษัท","rendered_pages":1,` +
			`"page_png_sizes":[{"width":612,"height":792,"bytes":19443}],"first_png_base64":"` +
			base64.StdEncoding.EncodeToString(png) + `"}`))
	})
	res, err := c.CheckWithFirstPNG(context.Background(), []byte("%PDF-1.7"))
	if err != nil {
		t.Fatal(err)
	}
	if res.Pages != 1 || res.Text != "สรุปใบแจ้งหนี้ของ บริษัท" || len(res.PagePNGSizes) != 1 ||
		res.PagePNGSizes[0] != (PagePNGSize{Width: 612, Height: 792, Bytes: 19443}) || !bytes.Equal(res.FirstPNG, png) {
		t.Fatalf("CheckResult = %+v", res)
	}
}

func TestConverterCheckRejectsBadJSON(t *testing.T) {
	for _, body := range []string{`{"pages": 1, "text": `, `<html>502</html>`, `{"pages":1,"page_png_sizes":[{},{}]}`} {
		c := converterServer(t, func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) })
		if _, err := c.Check(context.Background(), []byte("%PDF-1.7")); err == nil {
			t.Fatalf("%q must be an error", body)
		}
	}
}

func TestConverterHeaderFileName(t *testing.T) {
	cases := map[string]string{
		"r_v1.docx":             "r_v1.docx",
		`C:\jobs\out\deck.pptx`: "deck.pptx",
		"/job/out/รายงาน.DOCX":  "document.docx",
		"bad\r\nX-Evil: 1.xlsx": "document.xlsx",
	}
	for in, want := range cases {
		if got := headerFileName(in); got != want {
			t.Fatalf("headerFileName(%q) = %q, want %q", in, got, want)
		}
	}
}
