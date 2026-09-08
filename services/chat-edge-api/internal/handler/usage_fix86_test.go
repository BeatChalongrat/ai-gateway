package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// FIX86 Part B — "used" has one source: the account ledger (/portal/me).
// The request-log roll-up (/portal/me/usage/summary) is reported under
// its own name and never substitutes for the ledger, and a zero roll-up
// is a zero — not raw tokens dressed as credits.
func TestUsageMe_UsedIsTheLedgerAndTheRollupKeepsItsOwnName(t *testing.T) {
	gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/portal/me":
			_, _ = w.Write([]byte(`{"credit_balance": 52331072, "credit_used": 47818928}`))
		case "/portal/me/usage/summary":
			// tenant-wide roll-up: a DIFFERENT number (another member's usage is in it)
			_, _ = w.Write([]byte(`{"totals": {"requests": 1840, "tokens": 12335193, "credits_used": 51835899},
				"top_models": [{"model": "gpt-5.6-terra", "provider": "openai", "requests": 10, "tokens": 100, "credits_used": 29326341}]}`))
		default:
			w.WriteHeader(404)
		}
	}))
	defer gateway.Close()

	h := NewUsageHandler(gateway.URL + "/v1")
	req := httptest.NewRequest(http.MethodGet, "/usage/me", nil)
	req.Header.Set("Authorization", "Bearer portal-token")
	rec := httptest.NewRecorder()
	h.Me(rec, req)
	if rec.Code != 200 {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	var out usageResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if out.Credits.Used != 47818928 || out.Credits.Remaining != 52331072 || out.Credits.Total != 100150000 {
		t.Fatalf("credits must be the ledger: %+v", out.Credits)
	}
	if out.TotalCreditsUsed != 51835899 {
		t.Fatalf("the roll-up keeps its own field: %d", out.TotalCreditsUsed)
	}
	if out.Credits.Used == out.TotalCreditsUsed {
		t.Fatal("fixture must exercise the case where the two facts differ")
	}
	if len(out.UsageByModel) != 1 || out.UsageByModel[0].CreditsUsed != 29326341 {
		t.Fatalf("per-model rows are the roll-up's credits: %+v", out.UsageByModel)
	}
}

func TestUsageMe_ZeroRollupIsZeroNotTokens(t *testing.T) {
	gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/portal/me":
			_, _ = w.Write([]byte(`{"credit_balance": 1000, "credit_used": 0}`))
		case "/portal/me/usage/summary":
			// self-hosted models: logged cost 0 → credits 0, tokens plenty
			_, _ = w.Write([]byte(`{"totals": {"requests": 5, "tokens": 9000, "credits_used": 0},
				"top_models": [{"model": "qwen3.8-27b-fp8", "provider": "sranai", "requests": 5, "tokens": 9000, "credits_used": 0}]}`))
		default:
			w.WriteHeader(404)
		}
	}))
	defer gateway.Close()

	h := NewUsageHandler(gateway.URL + "/v1")
	req := httptest.NewRequest(http.MethodGet, "/usage/me", nil)
	req.Header.Set("Authorization", "Bearer portal-token")
	rec := httptest.NewRecorder()
	h.Me(rec, req)
	var out usageResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("%v: %s", err, rec.Body.String())
	}
	if out.TotalCreditsUsed != 0 {
		t.Fatalf("a zero roll-up used to become raw tokens (%d)", out.TotalCreditsUsed)
	}
	if out.UsageByModel[0].CreditsUsed != 0 || out.UsageByModel[0].Tokens != 9000 {
		t.Fatalf("tokens stay tokens, credits stay credits: %+v", out.UsageByModel[0])
	}
}
