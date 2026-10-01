package agentmarket

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// AM9 (I) — "send as me": an approved message goes from the person's own
// mailbox through the MCP gateway's mail server, called by chat-edge with the
// person's own token and chat-edge's internal token. What the person typed is
// the only source of recipients; a document steers nothing; the log says which
// transport carried it. With nothing configured, the draft is copied as before.

const personJWT = "person-portal-jwt-for-tests"

// fakeMailGateway is the backend's /portal/connectors and the MCP gateway's mail
// server in one: it answers the streamable-HTTP handshake and records the call.
type fakeMailGateway struct {
	mu        sync.Mutex
	srv       *httptest.Server
	connected bool
	available bool
	bearers   []string
	internals []string
	calls     []map[string]any
	ended     int
}

func newFakeMailGateway(t *testing.T, available, connected bool) *fakeMailGateway {
	f := &fakeMailGateway{available: available, connected: connected}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.bearers = append(f.bearers, r.Header.Get("Authorization"))
		switch {
		case r.URL.Path == "/portal/connectors":
			conns := []map[string]any{}
			if f.connected {
				conns = append(conns, map[string]any{"connector_id": "mail", "provider": "google", "connected": true})
			}
			avail := []string{}
			if f.available {
				avail = append(avail, "mail")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"connections": conns, "available": avail})
		case r.URL.Path == "/mcp/servers/mail/mcp" && r.Method == http.MethodDelete:
			f.ended++
		case r.URL.Path == "/mcp/servers/mail/mcp":
			f.internals = append(f.internals, r.Header.Get("X-Internal-Token"))
			var m map[string]any
			_ = json.NewDecoder(r.Body).Decode(&m)
			id, hasID := m["id"]
			if !hasID {
				w.WriteHeader(http.StatusAccepted)
				return
			}
			result := map[string]any{}
			if m["method"] == "initialize" {
				w.Header().Set("Mcp-Session-Id", "s-1")
				result = map[string]any{"protocolVersion": "2025-03-26", "capabilities": map[string]any{"tools": map[string]any{}}}
			} else if m["method"] == "tools/call" {
				p := m["params"].(map[string]any)
				f.calls = append(f.calls, p["arguments"].(map[string]any))
				result = map[string]any{"content": []any{map[string]any{"type": "text", "text": `{"sent":true,"id":"18f0","attachments":2}`}},
					"isError": false}
			}
			b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "event: message\ndata: %s\n\n", b)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeMailGateway) mailer() *MCPMail {
	return &MCPMail{Gateway: f.srv.URL, Portal: f.srv.URL, Token: func() string { return "chat-edge-internal" }}
}

func (e *testEnv) asPerson(method, path string, body any) map[string]any {
	e.t.Helper()
	code, out := e.do(method, path, body, "Authorization", "Bearer "+personJWT)
	if code != 200 {
		e.t.Fatalf("%s %s: %d %v", method, path, code, out)
	}
	return out
}

func TestAM9AnApprovedMessageGoesFromThePersonsOwnMailboxWithTheRoundsFiles(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	gw := newFakeMailGateway(t, true, true)
	e.api.personMail = gw.mailer()
	ctx := context.Background()
	e.seedDefinition()
	in := e.install()
	run, arts := e.deliveredRound(in, map[string]any{"period": "2026-09", "period_label": "กันยายน 2569", "revenue": "1250000.00"})
	d := draftOf(t, e.asPerson("POST", "/runs/"+run.ID.String()+"/email-draft", nil))
	if d["transport"] != "mailbox" || d["can_send"] != true {
		t.Fatalf("a connected mailbox sends the person's work: %v / %v", d["transport"], d["mailbox"])
	}
	out := e.asPerson("POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"cfo@company.example"},
		"cc": []string{"audit@company.example"}, "body_sha256": d["body_sha256"], "approve": true})
	if s := out["send"].(map[string]any); s["status"] != "sent" || s["transport"] != "mailbox" {
		t.Fatalf("the send: %v", out)
	}
	if len(gw.calls) != 1 {
		t.Fatalf("one call to the mail server: %d", len(gw.calls))
	}
	args := gw.calls[0]
	if fmt.Sprint(args["to"]) != "[cfo@company.example]" || fmt.Sprint(args["cc"]) != "[audit@company.example]" {
		t.Fatalf("recipients as typed: %v %v", args["to"], args["cc"])
	}
	if args["subject"] != d["subject"] || args["text"] != d["body"] {
		t.Fatalf("exactly the approved message:\n%v\n%v", args["subject"], args["text"])
	}
	atts := args["attachments"].([]any)
	if len(atts) != len(arts) {
		t.Fatalf("the round's %d files: %d attached", len(arts), len(atts))
	}
	for i, a := range atts {
		m := a.(map[string]any)
		data, _ := base64.StdEncoding.DecodeString(m["content_base64"].(string))
		body, _, _, err := e.api.files.Download(ctx, arts[i].StorageKey)
		if err != nil {
			t.Fatal(err)
		}
		want, _ := io.ReadAll(body)
		body.Close()
		if sum(data) != sum(want) || len(data) == 0 {
			t.Fatalf("%s: the bytes of the round's file", m["filename"])
		}
	}
	for _, b := range gw.bearers {
		if b != "Bearer "+personJWT {
			t.Fatalf("the person's own token, every call: %q", b)
		}
	}
	for _, x := range gw.internals {
		if x != "chat-edge-internal" {
			t.Fatalf("chat-edge's internal token on every call to the mail server: %q", x)
		}
	}
	if gw.ended != 1 {
		t.Fatalf("the session is ended: %d", gw.ended)
	}
	var transport string
	if err := e.store.Pool.QueryRow(ctx, `SELECT transport FROM agent_market.email_sends WHERE draft_id = $1`, d["id"]).Scan(&transport); err != nil ||
		transport != "mailbox" {
		t.Fatalf("our log says which transport carried it: %q %v", transport, err)
	}
}

func sum(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func TestAM9NoDocumentSteersAMessageThroughThePersonsMailbox(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	gw := newFakeMailGateway(t, true, true)
	e.api.personMail = gw.mailer()
	ctx := context.Background()
	e.seedDefinition()
	in := e.install()
	seller := "บริษัท " + steering[0] + " จำกัด"
	run, arts := e.deliveredRound(in, map[string]any{"period": "2026-09", "period_label": steering[2], "revenue": "1250000.00",
		"gate":         map[string]any{"passed": true, "what": "ลงตัว ผู้ขาย " + seller + " " + steering[1]},
		"not_included": []any{map[string]any{"doc_key": "d1", "name": steering[1], "reason": steering[3] + " " + steering[2]}}})
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_output_artifacts SET name = $2 WHERE id = $1`, arts[0].ID,
		steering[3]+" "+steering[2]+" "+steering[0]+".docx"); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET title = $2 WHERE id = $1`, in.ID, "รายงานการเงิน "+steering[0]); err != nil {
		t.Fatal(err)
	}
	d := draftOf(t, e.asPerson("POST", "/runs/"+run.ID.String()+"/email-draft", nil))
	e.asPerson("POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"owner@company.example"},
		"body_sha256": d["body_sha256"], "approve": true})
	if len(gw.calls) != 1 {
		t.Fatalf("calls: %d", len(gw.calls))
	}
	args := gw.calls[0]
	if fmt.Sprint(args["to"]) != "[owner@company.example]" || fmt.Sprint(args["cc"]) != "[]" {
		t.Fatalf("a document chose a recipient: to %v cc %v", args["to"], args["cc"])
	}
	t.Logf("ASSERT the mailbox was asked to send to exactly what the person typed: %v", args["to"])
	var names []string
	for _, a := range args["attachments"].([]any) {
		names = append(names, a.(map[string]any)["filename"].(string))
	}
	for _, bad := range []string{"evil.example", "http", "mailto", "www.", "C:\\", "secret.xlsx"} {
		if strings.Contains(fmt.Sprint(args["subject"])+"\n"+fmt.Sprint(args["text"])+"\n"+strings.Join(names, "\n"), bad) {
			t.Fatalf("the message carries %q:\n%v\n%v\n%v", bad, args["subject"], args["text"], names)
		}
		t.Logf("ASSERT the sent message (subject, body, attachment names) has no %q", bad)
	}
	if len(names) != len(arts) {
		t.Fatalf("attachments %v for %d artifacts", names, len(arts))
	}
	t.Logf("ASSERT the attachments are the round's %d artifacts: %v", len(arts), names)
}

func TestAM9WithNothingConfiguredADraftIsCopiedAndAMailboxNotConnectedFallsToTheRelay(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	in := e.install()
	run, _ := e.deliveredRound(in, map[string]any{"period": "2026-09", "period_label": "กันยายน 2569", "revenue": "1250000.00"})
	// nothing configured: no mailbox offered, no relay — the draft says so and is copied
	e.api.personMail = personMailFrom(Config{})
	d := draftOf(t, e.asPerson("POST", "/runs/"+run.ID.String()+"/email-draft", nil))
	box := d["mailbox"].(map[string]any)
	if d["can_send"] != false || d["transport"] != "" || box["available"] != false || box["connected"] != false {
		t.Fatalf("nothing configured: %v", d)
	}
	code, out := e.do("POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"a@company.example"},
		"body_sha256": d["body_sha256"], "approve": true}, "Authorization", "Bearer "+personJWT)
	if code != 409 {
		t.Fatalf("nothing to send with: %d %v", code, out)
	}
	// the deployment can connect a mailbox, the person has not: the relay sends on their behalf, and the
	// draft says the mailbox can be connected
	gw := newFakeMailGateway(t, true, false)
	e.api.personMail = gw.mailer()
	sink := newSMTPSink(t)
	e.relayOn(sink, "none", "")
	d = draftOf(t, e.asPerson("GET", "/email-drafts/"+d["id"].(string), nil))
	box = d["mailbox"].(map[string]any)
	if d["transport"] != "organisation" || box["available"] != true || box["connected"] != false {
		t.Fatalf("a mailbox that can be connected, a relay that sends: %v", d)
	}
	out = e.asPerson("POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"a@company.example"},
		"body_sha256": d["body_sha256"], "approve": true})
	if s := out["send"].(map[string]any); s["transport"] != "organisation" || s["status"] != "sent" || len(gw.calls) != 0 || len(sink.messages()) != 1 {
		t.Fatalf("the relay carried it: %v (mailbox calls %d)", out, len(gw.calls))
	}
}
