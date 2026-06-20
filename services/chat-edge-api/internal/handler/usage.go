package handler

// Usage endpoint — GET /api/v1/usage/me.
//
// Web-chat's credit badge + usage popover need the caller's credit
// balance and per-model breakdown. Both already exist on the gateway's
// portal API (GET /portal/me and GET /portal/me/usage/summary — the
// same endpoints the Portal Dashboard reads, so the numbers always
// match). This handler is a thin authenticated proxy: it forwards the
// caller's own portal JWT as a Bearer header and reshapes the two
// responses into one usage payload. No usage data is duplicated in
// chat-edge.
//
// Credit semantics (portal convention): 1 credit = 1 token.
// credit_balance is the REMAINING prepaid balance, credit_used the
// lifetime debit sum, total allotment = balance + used. Credits are a
// lifetime pool — there is no reset date.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"strings"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
)

type UsageHandler struct {
	// gatewayBase is the gateway root (no /v1 suffix) — portal routes
	// live at {base}/portal/*.
	gatewayBase string
	client      *http.Client
}

// NewUsageHandler derives the portal-API root from the configured
// gateway API URL (which carries a /v1 suffix for the proxy routes).
func NewUsageHandler(gatewayAPIURL string) *UsageHandler {
	base := strings.TrimSuffix(strings.TrimRight(gatewayAPIURL, "/"), "/v1")
	return &UsageHandler{
		gatewayBase: base,
		client:      &http.Client{Timeout: 10 * time.Second},
	}
}

// Wire types — the exact shape the web-chat popover consumes.

type usageCredits struct {
	Total     int64   `json:"total"`
	Used      int64   `json:"used"`
	Remaining int64   `json:"remaining"`
	ResetDate *string `json:"reset_date"` // always null — lifetime pool
}

type usageModelRow struct {
	Model        string  `json:"model"`
	Provider     string  `json:"provider"`
	Tokens       int64   `json:"tokens"`
	CreditsUsed  int64   `json:"credits_used"`
	RequestCount int64   `json:"request_count"`
	Percentage   float64 `json:"percentage"`
}

type usageResponse struct {
	Credits      usageCredits    `json:"credits"`
	UsageByModel []usageModelRow `json:"usage_by_model"`
	// TotalCreditsUsed sums display credits across ALL models (not just
	// the top-5 rows) — drives the panel's "Total used" footer.
	TotalCreditsUsed int64  `json:"total_credits_used"`
	Period           string `json:"period"`
}

// Gateway response slices (only the fields we read).

type portalMe struct {
	CreditBalance int64 `json:"credit_balance"`
	CreditUsed    int64 `json:"credit_used"`
}

type portalUsageSummary struct {
	Totals struct {
		Requests    int64 `json:"requests"`
		Tokens      int64 `json:"tokens"`
		CreditsUsed int64 `json:"credits_used"`
	} `json:"totals"`
	TopModels []struct {
		Model       string `json:"model"`
		Provider    string `json:"provider"`
		Requests    int64  `json:"requests"`
		Tokens      int64  `json:"tokens"`
		CreditsUsed int64  `json:"credits_used"`
	} `json:"top_models"`
}

// Me serves GET /usage/me.
func (h *UsageHandler) Me(w http.ResponseWriter, r *http.Request) {
	token, err := middleware.ExtractPortalToken(r)
	if err != nil {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "unauthorized")
		return
	}

	var me portalMe
	if err := h.getJSON(r.Context(), token, "/portal/me", &me); err != nil {
		slog.Warn("usage_proxy_me_failed", "err", err.Error())
		WriteError(w, http.StatusBadGateway, "USAGE_UNAVAILABLE", "usage data unavailable")
		return
	}
	var summary portalUsageSummary
	if err := h.getJSON(r.Context(), token, "/portal/me/usage/summary", &summary); err != nil {
		slog.Warn("usage_proxy_summary_failed", "err", err.Error())
		WriteError(w, http.StatusBadGateway, "USAGE_UNAVAILABLE", "usage data unavailable")
		return
	}

	rows := make([]usageModelRow, 0, len(summary.TopModels))
	for _, m := range summary.TopModels {
		// Display credits come from the gateway (real logged cost ×
		// USAGE_CREDITS_PER_USD). Fallback to raw tokens (the legacy
		// 1 credit = 1 token convention) when talking to an older
		// gateway that doesn't send credits yet.
		credits := m.CreditsUsed
		totalCredits := summary.Totals.CreditsUsed
		if totalCredits == 0 {
			credits = m.Tokens
			totalCredits = summary.Totals.Tokens
		}
		pct := 0.0
		if totalCredits > 0 {
			pct = math.Round(float64(credits)/float64(totalCredits)*1000) / 10
		}
		rows = append(rows, usageModelRow{
			Model:        m.Model,
			Provider:     m.Provider,
			Tokens:       m.Tokens,
			CreditsUsed:  credits,
			RequestCount: m.Requests,
			Percentage:   pct,
		})
	}
	// Most-used first by credits so the popover's color ranking is
	// stable (summary sorts by request count, which can differ).
	for i := 1; i < len(rows); i++ {
		for j := i; j > 0 && rows[j].CreditsUsed > rows[j-1].CreditsUsed; j-- {
			rows[j], rows[j-1] = rows[j-1], rows[j]
		}
	}

	totalCredits := summary.Totals.CreditsUsed
	if totalCredits == 0 {
		totalCredits = summary.Totals.Tokens
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(usageResponse{
		Credits: usageCredits{
			Total:     me.CreditBalance + me.CreditUsed,
			Used:      me.CreditUsed,
			Remaining: me.CreditBalance,
			ResetDate: nil,
		},
		UsageByModel:     rows,
		TotalCreditsUsed: totalCredits,
		Period:           "lifetime",
	})
}

func (h *UsageHandler) getJSON(ctx context.Context, token, path string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, h.gatewayBase+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := h.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("gateway %s: status=%d body=%.200s", path, resp.StatusCode, string(body))
	}
	return json.Unmarshal(body, out)
}
