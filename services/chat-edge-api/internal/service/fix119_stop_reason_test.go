package service

// FIX119 Part B — why the model stopped travels with the Done, and the
// closed set is pinned to the contract.

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
)

func TestStopReasons_PinnedToContract(t *testing.T) {
	raw, err := os.ReadFile(terminalContractPath)
	if err != nil {
		t.Fatalf("contract: %v", err)
	}
	var c struct {
		StopReasons   []string                     `json:"stop_reasons"`
		StopReasonMap map[string]map[string]string `json:"stop_reason_map"`
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(c.StopReasons) != fmt.Sprint(StopReasons) {
		t.Fatalf("Go stop reasons %v != contract %v", StopReasons, c.StopReasons)
	}
	for _, r := range c.StopReasons {
		if !IsStopReason(r) {
			t.Fatalf("IsStopReason(%q) = false", r)
		}
	}
	if IsStopReason("length") || IsStopReason("") {
		t.Fatal("an OpenAI value or an empty string is not a stop reason")
	}
	// The fallback table IS the contract's openai row, nothing more.
	if fmt.Sprint(c.StopReasonMap["openai"]) != fmt.Sprint(openAIFinishToStopReason) {
		t.Fatalf("fallback table %v != contract openai row %v", openAIFinishToStopReason, c.StopReasonMap["openai"])
	}
}

func TestStopReasonFromFinish(t *testing.T) {
	for finish, want := range map[string]string{
		"stop": StopReasonEndTurn, "length": StopReasonMaxTokens, "tool_calls": StopReasonToolUse,
		"content_filter": StopReasonContentFilter, "": "", "something_new": StopReasonUnknown,
	} {
		if got := StopReasonFromFinish(finish); got != want {
			t.Fatalf("StopReasonFromFinish(%q) = %q, want %q", finish, got, want)
		}
	}
}

// The gateway's final chunk carries stop_reason; the pump forwards it on
// the Done (before FIX119 the Done carried neither finish nor stop reason,
// because only delta-bearing chunks were forwarded).
func TestPump_DoneCarriesStopReasonAndIds(t *testing.T) {
	body := `data: {"id":"req_abc123","choices":[{"delta":{"content":"cut off he"},"finish_reason":null}]}` + "\n\n" +
		`data: {"id":"req_abc123","provider_request_id":"prov-77","choices":[{"delta":{},"finish_reason":"length","stop_reason":"max_tokens"}]}` + "\n\n" +
		"data: [DONE]\n\n"
	var done *StreamUpdate
	for _, u := range pumpOnce(t, body) {
		if u.Done {
			u := u
			done = &u
		}
	}
	if done == nil {
		t.Fatal("no Done")
	}
	if done.FinishReason != "length" || done.StopReason != StopReasonMaxTokens {
		t.Fatalf("Done finish/stop = %q/%q", done.FinishReason, done.StopReason)
	}
	if done.GatewayRequestID != "req_abc123" || done.ProviderRequestID != "prov-77" {
		t.Fatalf("Done ids = %q/%q", done.GatewayRequestID, done.ProviderRequestID)
	}
}

// An older gateway sends only finish_reason: the OpenAI row of the
// contract fills stop_reason in; a value it does not name is unknown.
func TestPump_DoneFallsBackFromFinishReason(t *testing.T) {
	cases := map[string]string{"stop": StopReasonEndTurn, "length": StopReasonMaxTokens, "weird": StopReasonUnknown}
	for finish, want := range cases {
		body := `data: {"choices":[{"delta":{"content":"x"},"finish_reason":null}]}` + "\n\n" +
			`data: {"choices":[{"delta":{},"finish_reason":"` + finish + `"}]}` + "\n\n" + "data: [DONE]\n\n"
		got := ""
		for _, u := range pumpOnce(t, body) {
			if u.Done {
				got = u.StopReason
			}
		}
		if got != want {
			t.Fatalf("finish %q → stop %q, want %q", finish, got, want)
		}
	}
}
