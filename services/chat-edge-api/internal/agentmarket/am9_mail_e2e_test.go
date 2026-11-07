package agentmarket

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/mail"
	"os"
	"strings"
	"testing"
	"time"
)

// AM9 (I) end to end, outside the unit lane: chat-edge's own mail client
// (MCPMail) against the REAL MCP gateway image — its proxy, its per-person
// child, servers/mail-send.js — and a stand-in for the backend's broker and
// the Gmail API (the AM9 mail probe's fake Google, kept with the probes on the
// author's machine). Runs only when that probe sets AM9_MAIL_E2E_GATEWAY; skipped otherwise.
func TestAM9MailEndToEndThroughTheRealGateway(t *testing.T) {
	gw := os.Getenv("AM9_MAIL_E2E_GATEWAY")
	if gw == "" {
		t.Skip("AM9_MAIL_E2E_GATEWAY not set (the AM9 mail probe sets it)")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	jwt := mintPortalJWT(os.Getenv("AM9_MAIL_E2E_JWT_SECRET"), "e2e-person")
	file, err := os.ReadFile(os.Getenv("AM9_MAIL_E2E_FILE"))
	if err != nil {
		t.Fatal(err)
	}
	internal := os.Getenv("AM9_MAIL_E2E_INTERNAL")
	m := &MCPMail{Gateway: gw, Portal: os.Getenv("AM9_MAIL_E2E_PORTAL"), Token: func() string { return internal }}
	st, err := m.Status(ctx, jwt)
	if err != nil || !st.Available || !st.Connected {
		t.Fatalf("the person's mailbox: %+v %v", st, err)
	}
	// without chat-edge's internal token the gateway answers no one
	wrong := &MCPMail{Gateway: gw, Portal: m.Portal, Token: func() string { return "not-the-token" }}
	_, refused := wrong.Send(ctx, jwt, MailMessage{To: []string{"x@company.example"}, Subject: "x", Body: "x"})
	if refused == nil {
		t.Fatal("the gateway sent for a caller without chat-edge's token")
	}
	t.Logf("ASSERT the gateway refuses the mail server to a caller without chat-edge's token: %v", refused)
	subject := "กระทบยอดธนาคาร · กันยายน 2569 · ลงตัว"
	id, err := m.Send(ctx, jwt, MailMessage{To: []string{"cfo@company.example"}, Cc: []string{"audit@company.example"}, Subject: subject,
		Body: "สรุปงานกระทบยอดเดือนกันยายน 2569 แนบกระดาษทำการ",
		Attachments: []MailAttachment{{Name: "กระทบยอด_2569-09.xlsx", ContentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
			Data: file}}})
	if err != nil || id == "" {
		t.Fatalf("send: %q %v", id, err)
	}
	t.Logf("ASSERT sent through the real gateway and the real mail server, message id %s", id)
	raw, err := os.ReadFile(os.Getenv("AM9_MAIL_E2E_RAW"))
	if err != nil {
		t.Fatal(err)
	}
	msg, err := mail.ReadMessage(strings.NewReader(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	dec := new(mime.WordDecoder)
	got, _ := dec.DecodeHeader(msg.Header.Get("Subject"))
	if got != subject || msg.Header.Get("To") != "cfo@company.example" || msg.Header.Get("Cc") != "audit@company.example" || msg.Header.Get("From") != "" {
		t.Fatalf("headers: subject %q to %q cc %q from %q", got, msg.Header.Get("To"), msg.Header.Get("Cc"), msg.Header.Get("From"))
	}
	_, params, _ := mime.ParseMediaType(msg.Header.Get("Content-Type"))
	mr := multipart.NewReader(msg.Body, params["boundary"])
	found := false
	for {
		p, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(p.Header.Get("Content-Disposition"), "attachment") {
			continue
		}
		b, _ := io.ReadAll(p)
		data, err := base64.StdEncoding.DecodeString(strings.Join(strings.Fields(string(b)), ""))
		if err != nil {
			t.Fatal(err)
		}
		if sum(data) != sum(file) {
			t.Fatalf("the attachment's bytes are not the file's: %d vs %d bytes", len(data), len(file))
		}
		found = true
		t.Logf("ASSERT the attachment arrived as a real attachment, %d bytes, sha256 %s = the file's", len(data), sum(data)[:16])
	}
	if !found {
		t.Fatal("no attachment in the message")
	}
	var req struct {
		Type   string `json:"type"`
		Bearer bool   `json:"bearer_is_person_token"`
	}
	b, _ := os.ReadFile(strings.TrimSuffix(os.Getenv("AM9_MAIL_E2E_RAW"), "raw.eml") + "request.json")
	_ = json.Unmarshal(b, &req)
	if req.Type != "message/rfc822" || !req.Bearer {
		t.Fatalf("the Gmail endpoint got %+v", req)
	}
	t.Log("ASSERT the Gmail endpoint was called with the person's own mailbox token, fetched by the gateway from the broker")
}

func mintPortalJWT(secret, sub string) string {
	enc := base64.RawURLEncoding
	head := enc.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	body, _ := json.Marshal(map[string]any{"sub": sub, "type": "portal", "token_use": "access", "tenant_id": "t-e2e",
		"exp": time.Now().Add(time.Hour).Unix()})
	payload := enc.EncodeToString(body)
	h := hmac.New(sha256.New, []byte(secret))
	h.Write([]byte(head + "." + payload))
	return head + "." + payload + "." + enc.EncodeToString(h.Sum(nil))
}
