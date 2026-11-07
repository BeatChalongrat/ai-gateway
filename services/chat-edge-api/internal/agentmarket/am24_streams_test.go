package agentmarket

import (
	"bytes"
	"context"
	"net/http"
	"testing"
	"time"
)

// AM24 Part 2 — an agent's page left open does not hold the old chat-edge at a swap. The swap of 17 Oct 2026 left
// chat-edge 11.7 s without an answer: the old copy took 11.4 s to stop, because http.Server.Shutdown waits for every
// open request and a thread's stream (events) runs until its page goes or 10 minutes pass. EndStreams, registered
// with the server's RegisterOnShutdown, ends each one with the reconnect it already sends at its deadline.
func TestAThreadStreamEndsWithAReconnectWhenTheServerShutsDown(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	_, th := e.do("GET", "/installs/"+in.ID.String()+"/thread", nil)
	cursor := int64(th["cursor"].(float64))
	e.srv.Config.RegisterOnShutdown(e.api.EndStreams) // (cmd/server/main.go does the same)

	req, _ := http.NewRequest("GET", e.srv.URL+"/agent-market/installs/"+in.ID.String()+"/events?after="+itoa(int(cursor)), nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	first := make([]byte, 256)
	if n, _ := resp.Body.Read(first); !bytes.Contains(first[:n], []byte("agent-market stream")) {
		t.Fatalf("the stream did not open: %q", first[:n])
	}

	got := make(chan []byte, 1)
	go func() {
		var all []byte
		buf := make([]byte, 4096)
		for {
			n, err := resp.Body.Read(buf)
			all = append(all, buf[:n]...)
			if err != nil {
				got <- all
				return
			}
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	start := time.Now()
	err = e.srv.Config.Shutdown(ctx)
	took := time.Since(start)
	if err != nil || took > 2*time.Second {
		t.Fatalf("the shutdown waited for the open stream: %v after %v (it must end the stream, not wait for it)", err, took)
	}
	select {
	case body := <-got:
		if !bytes.Contains(body, []byte("event: reconnect")) {
			t.Fatalf("the page was not told to reconnect: %q", body)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the stream did not end")
	}
	EndStreamsTwiceIsHarmless(t, e.api)
}

// EndStreamsTwiceIsHarmless: a second shutdown signal (or a second call) closes nothing twice.
func EndStreamsTwiceIsHarmless(t *testing.T, a *API) {
	t.Helper()
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("a second EndStreams panicked: %v", r)
		}
	}()
	a.EndStreams()
	a.EndStreams()
}
