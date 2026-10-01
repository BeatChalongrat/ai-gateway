package agentmarket

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ── "Send as me" (AM9) ───────────────────────────────────────────────────
//
// SMTP (AM7, mail.go) is the system writing to a person — reminders, "your
// round is ready" — from the organisation's address. A person sending their
// own work should send it from their own address, into their own Sent folder:
// that is the mail connector. The send happens here, in chat-edge, calling the
// MCP gateway (services/mcp-gateway, server "mail"), which spawns that
// person's child with their provider token in its environment only — never
// the runner, which has no network egress, and never a model: the mail
// server is in no catalogue a model's tools come from, and the gateway serves
// it only to a caller holding chat-edge's internal token. The message is the
// one the person saw and approved (email.go sendEmail), recipients as typed.
//
// With nothing configured (no gateway, no token, or no OAuth client on the
// backend) a person's mailbox is simply not offered: the relay sends on
// their behalf if an admin set one up, else the draft is copied, as before.

// MailboxStatus is whether this person's own mailbox can send now.
type MailboxStatus struct {
	// Available: this deployment can connect a mailbox (gateway set, OAuth
	// client configured on the backend).
	Available bool `json:"available"`
	// Connected: this person connected theirs.
	Connected bool `json:"connected"`
}

// PersonMailer sends from a person's own mailbox; bearer is their portal token.
type PersonMailer interface {
	Status(ctx context.Context, bearer string) (MailboxStatus, error)
	Send(ctx context.Context, bearer string, msg MailMessage) (string, error)
}

// ErrMailboxNotConnected: the person's mailbox token is missing or dead.
var ErrMailboxNotConnected = errors.New("mailbox not connected")

// MCPMail is the mail connector through the MCP gateway.
type MCPMail struct {
	// Gateway — AGENT_MARKET_MAIL_GATEWAY_URL (http://mcp-gateway:3200).
	Gateway string
	// Token — AGENT_MARKET_MAIL_GATEWAY_TOKEN: the gateway's MCP_INTERNAL_TOKEN.
	Token func() string
	// Portal — the backend whose /portal/connectors says what is connected.
	Portal string
	HTTP   *http.Client
}

func (m *MCPMail) client() *http.Client {
	if m.HTTP != nil {
		return m.HTTP
	}
	return &http.Client{Timeout: 90 * time.Second}
}

func (m *MCPMail) configured() bool {
	return m != nil && m.Gateway != "" && m.Token != nil && m.Token() != ""
}

// Status asks the backend which connectors exist and which this person connected.
func (m *MCPMail) Status(ctx context.Context, bearer string) (MailboxStatus, error) {
	if !m.configured() || bearer == "" {
		return MailboxStatus{}, nil
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(m.Portal, "/")+"/portal/connectors", nil)
	if err != nil {
		return MailboxStatus{}, err
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	resp, err := m.client().Do(req)
	if err != nil {
		return MailboxStatus{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return MailboxStatus{}, fmt.Errorf("portal connectors: %d", resp.StatusCode)
	}
	var body struct {
		Connections []struct {
			ConnectorID string `json:"connector_id"`
			Connected   bool   `json:"connected"`
		} `json:"connections"`
		Available []string `json:"available"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&body); err != nil {
		return MailboxStatus{}, err
	}
	var st MailboxStatus
	for _, id := range body.Available {
		st.Available = st.Available || id == "mail"
	}
	for _, c := range body.Connections {
		st.Connected = st.Connected || (c.ConnectorID == "mail" && c.Connected)
	}
	st.Connected = st.Connected && st.Available
	return st, nil
}

// Send calls the gateway's mail server once: initialize, initialized,
// tools/call send_message, end the session.
func (m *MCPMail) Send(ctx context.Context, bearer string, msg MailMessage) (string, error) {
	if !m.configured() {
		return "", errors.New("the mail connector is not configured")
	}
	atts := []map[string]string{}
	for _, a := range msg.Attachments {
		atts = append(atts, map[string]string{"filename": a.Name, "mime_type": a.ContentType,
			"content_base64": base64.StdEncoding.EncodeToString(a.Data)})
	}
	args := map[string]any{"to": msg.To, "cc": nonNil(msg.Cc), "subject": msg.Subject, "text": msg.Body, "attachments": atts}
	s := &mcpSession{m: m, bearer: bearer}
	if _, err := s.call(ctx, 1, "initialize", map[string]any{"protocolVersion": "2025-03-26", "capabilities": map[string]any{},
		"clientInfo": map[string]any{"name": "sran-chat-edge", "version": "am9"}}); err != nil {
		return "", err
	}
	defer s.close()
	_ = s.notify(ctx, "notifications/initialized")
	res, err := s.call(ctx, 2, "tools/call", map[string]any{"name": "send_message", "arguments": args})
	if err != nil {
		return "", err
	}
	var out struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
		IsError bool `json:"isError"`
	}
	if err := json.Unmarshal(res, &out); err != nil {
		return "", err
	}
	text := ""
	if len(out.Content) > 0 {
		text = out.Content[0].Text
	}
	if out.IsError {
		if text == "not_connected" {
			return "", ErrMailboxNotConnected
		}
		return "", fmt.Errorf("the mailbox did not send: %s", TrimText(text, 120))
	}
	var sent struct {
		ID string `json:"id"`
	}
	_ = json.Unmarshal([]byte(text), &sent)
	return sent.ID, nil
}

type mcpSession struct {
	m      *MCPMail
	bearer string
	id     string
}

func (s *mcpSession) post(ctx context.Context, method string, body []byte) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(s.m.Gateway, "/")+"/mcp/servers/mail/mcp", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+s.bearer)
	req.Header.Set("X-Internal-Token", s.m.Token())
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if s.id != "" {
		req.Header.Set("Mcp-Session-Id", s.id)
	}
	return s.m.client().Do(req)
}

func (s *mcpSession) notify(ctx context.Context, method string) error {
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": method})
	resp, err := s.post(ctx, http.MethodPost, b)
	if err != nil {
		return err
	}
	resp.Body.Close()
	return nil
}

func (s *mcpSession) close() {
	if s.id == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if resp, err := s.post(ctx, http.MethodDelete, nil); err == nil {
		resp.Body.Close()
	}
}

// call sends one request and reads its answer, as JSON or as an event stream.
func (s *mcpSession) call(ctx context.Context, id int, method string, params any) (json.RawMessage, error) {
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "method": method, "params": params})
	resp, err := s.post(ctx, http.MethodPost, b)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if v := resp.Header.Get("Mcp-Session-Id"); v != "" {
		s.id = v
	}
	if resp.StatusCode == http.StatusUnauthorized {
		return nil, ErrMailboxNotConnected
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("mail gateway: %d", resp.StatusCode)
	}
	var msgs [][]byte
	if strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		sc := bufio.NewScanner(io.LimitReader(resp.Body, 4<<20))
		sc.Buffer(make([]byte, 64<<10), 4<<20)
		for sc.Scan() {
			if line := sc.Text(); strings.HasPrefix(line, "data:") {
				msgs = append(msgs, []byte(strings.TrimSpace(strings.TrimPrefix(line, "data:"))))
			}
		}
	} else {
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		if err != nil {
			return nil, err
		}
		msgs = append(msgs, raw)
	}
	for _, raw := range msgs {
		var r struct {
			ID     *int            `json:"id"`
			Result json.RawMessage `json:"result"`
			Error  *struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		if json.Unmarshal(raw, &r) != nil || r.ID == nil || *r.ID != id {
			continue
		}
		if r.Error != nil {
			return nil, fmt.Errorf("mail gateway: %s", TrimText(r.Error.Message, 120))
		}
		return r.Result, nil
	}
	return nil, errors.New("mail gateway: no answer")
}
