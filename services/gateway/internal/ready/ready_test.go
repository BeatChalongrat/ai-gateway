package ready

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type pinger struct{ err error }

func (p pinger) Ping(context.Context) error { return p.err }

func backend(status int, body string) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ready" {
			http.NotFound(w, r)
			return
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
}

func probe(t *testing.T, c Checker) (int, map[string]any) {
	t.Helper()
	rec := httptest.NewRecorder()
	c.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/__gw/ready", nil))
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	return rec.Code, body
}

func TestFIX103_ReadyOnlyWhenTheBackendIsReady(t *testing.T) {
	srv := backend(http.StatusOK, `{"status":"ready","failures":[],"degraded":[]}`)
	defer srv.Close()
	code, body := probe(t, Checker{DB: pinger{}, BackendURL: srv.URL + "/", Client: srv.Client()})
	if code != http.StatusOK || body["status"] != "ready" {
		t.Fatalf("code %d body %v", code, body)
	}
}

// The case that went unseen three rounds running: the sidecar is down, the
// backend still masks on its Python fallback. Ready — and says why it is
// degraded — rather than a 503 for a working service.
func TestFIX103_SidecarDownIsDegradedNotNotReady(t *testing.T) {
	srv := backend(http.StatusOK, `{"status":"ready","failures":[],"degraded":["dlp_engine"]}`)
	defer srv.Close()
	code, body := probe(t, Checker{DB: pinger{}, BackendURL: srv.URL, Client: srv.Client()})
	if code != http.StatusOK {
		t.Fatalf("code %d", code)
	}
	if d, _ := body["degraded"].([]any); len(d) != 1 || d[0] != "dlp_engine" {
		t.Fatalf("degraded not passed through: %v", body)
	}
}

func TestFIX103_BackendNotReadyIsNotReadyAndSaysWhy(t *testing.T) {
	srv := backend(http.StatusServiceUnavailable, `{"status":"not-ready","failures":["dlp_engine","redis"]}`)
	defer srv.Close()
	rec := httptest.NewRecorder()
	Checker{DB: pinger{}, BackendURL: srv.URL, Client: srv.Client()}.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/__gw/ready", nil))
	if rec.Code != http.StatusServiceUnavailable || !strings.Contains(rec.Body.String(), "dlp_engine, redis") {
		t.Fatalf("code %d body %s", rec.Code, rec.Body.String())
	}
}

// Before FIX103 this was 200: only Postgres was checked.
func TestFIX103_BackendUnreachableIsNotReady(t *testing.T) {
	srv := backend(http.StatusOK, `{}`)
	url := srv.URL
	srv.Close()
	code, _ := probe(t, Checker{DB: pinger{}, BackendURL: url, Client: http.DefaultClient})
	if code != http.StatusServiceUnavailable {
		t.Fatalf("a gateway whose backend is gone reported %d", code)
	}
}

func TestFIX103_PostgresDownIsStillNotReady(t *testing.T) {
	srv := backend(http.StatusOK, `{"status":"ready"}`)
	defer srv.Close()
	code, _ := probe(t, Checker{DB: pinger{err: errors.New("down")}, BackendURL: srv.URL, Client: srv.Client()})
	if code != http.StatusServiceUnavailable {
		t.Fatalf("code %d", code)
	}
}
