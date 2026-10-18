package main

import (
	"os"
	"strings"
	"testing"
)

// AM24 Part 2 — the wiring of the fix (CARD-RULES §6d): Agent Market's thread streams end at the server's shutdown
// only if the server registers them to. agentmarket/am24_streams_test.go proves EndStreams ends a stream; this proves
// the server still calls it — without it the old chat-edge waits its full 10 s for an agent's open page again.
func TestTheServerEndsAgentMarketStreamsWhenItShutsDown(t *testing.T) {
	src, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"amEndStreams = amAPI.EndStreams", "srv.RegisterOnShutdown(amEndStreams)"} {
		if !strings.Contains(string(src), want) {
			t.Errorf("main.go no longer has %q", want)
		}
	}
}
