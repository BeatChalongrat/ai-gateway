package agentmarket

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// Thread payloads — the shapes the web workspace renders. Kept in one
// place so both planes write exactly what the UI reads.

// Greeting states the agent's job and the files it needs.
type Greeting struct {
	Text     string       `json:"text"`
	Role     string       `json:"role"`
	Tagline  string       `json:"tagline"`
	Needs    []GreetNeed  `json:"needs"`
	Produces []GreetOut   `json:"produces"`
	Cannot   string       `json:"cannot"`
	Icon     string       `json:"icon"`
	Slug     string       `json:"slug"`
	Version  string       `json:"version"`
	Extra    *GreetStatus `json:"status,omitempty"`
}

// GreetNeed is one input the agent asks for.
type GreetNeed struct {
	Role     string `json:"role"`
	Label    string `json:"label"`
	Note     string `json:"note"`
	Required bool   `json:"required"`
}

// GreetOut is one output format.
type GreetOut struct {
	Format string `json:"format"`
	Label  string `json:"label"`
}

// GreetStatus carries install-time facts (e.g. the self-test run).
type GreetStatus struct {
	SelfTestRunID string `json:"self_test_run_id,omitempty"`
}

// GreetingFor builds the greeting from the manifest.
func GreetingFor(m Manifest) Greeting {
	g := Greeting{Text: m.Greeting.Text, Role: m.Greeting.Role, Tagline: m.Greeting.Tagline, Cannot: m.Cannot,
		Icon: m.Icon, Slug: m.ID, Version: m.Version}
	for _, in := range m.Inputs {
		g.Needs = append(g.Needs, GreetNeed{Role: in.Role, Label: in.Label, Note: in.Note, Required: in.Required})
	}
	for _, o := range m.Outputs {
		g.Produces = append(g.Produces, GreetOut{Format: o.Format, Label: o.Label})
	}
	return g
}

// CardPayload is a card as the thread shows it.
type CardPayload struct {
	CardID      string          `json:"card_id"`
	RunID       string          `json:"run_id"`
	Kind        string          `json:"kind"`
	Moment      string          `json:"moment"`
	Blocking    bool            `json:"blocking"`
	Status      string          `json:"status"`
	Intro       string          `json:"intro,omitempty"`
	Questions   json.RawMessage `json:"questions"`
	Answer      json.RawMessage `json:"answer,omitempty"`
	AnsweredVia string          `json:"answered_via,omitempty"`
	Attempts    int             `json:"attempts"`
	Note        string          `json:"note,omitempty"`
	Supersedes  string          `json:"supersedes,omitempty"`
	Files       json.RawMessage `json:"files,omitempty"` // late_file: the files in question
}

// ProgressStep is one line of the inline progress card.
type ProgressStep struct {
	Key    string `json:"key"`
	Label  string `json:"label"`
	Status string `json:"status"` // pending | running | done | waiting | failed | skipped
	Detail string `json:"detail,omitempty"`
}

// Progress is the inline progress card of a run.
type Progress struct {
	RunID      string         `json:"run_id"`
	Kind       string         `json:"kind"`
	Status     string         `json:"status"`
	Reason     string         `json:"reason,omitempty"`
	StartedAt  *time.Time     `json:"started_at,omitempty"`
	FinishedAt *time.Time     `json:"finished_at,omitempty"`
	ElapsedMS  int64          `json:"elapsed_ms"`
	AwaitingMS int64          `json:"awaiting_ms"`
	Steps      []ProgressStep `json:"steps"`
	Summary    map[string]any `json:"summary,omitempty"`
}

// OutputsPayload is the delivered-files card.
type OutputsPayload struct {
	RunID       string           `json:"run_id"`
	PeriodLabel string           `json:"period_label"`
	Artifacts   []OutputArtifact `json:"artifacts"`
	Summary     map[string]any   `json:"summary,omitempty"`
	Model       map[string]any   `json:"model,omitempty"`
	Credits     float64          `json:"credits"`
	Rendered    bool             `json:"rerender,omitempty"`
	// Review (AM3): these files carry a reviewer's sign-off on their face.
	Review *OutputsReview `json:"review,omitempty"`
	// Draft (AM5): the package's completion gate did not pass — the files are
	// delivered for the person to see why, and say "draft" on their face.
	// Gate is what the gate said, in the package's words.
	Draft bool         `json:"draft,omitempty"`
	Gate  *OutputsGate `json:"gate,omitempty"`
}

// OutputsGate is a package's completion gate as the files card shows it.
type OutputsGate struct {
	Passed bool   `json:"passed"`
	What   string `json:"what"`
	Next   string `json:"next,omitempty"`
}

// OutputsReview is the sign-off an outputs card was re-issued with.
type OutputsReview struct {
	ID       string   `json:"id"`
	Reviewer string   `json:"reviewer"`
	AtLabel  string   `json:"at_label"`
	Note     string   `json:"note,omitempty"`
	Skipped  []string `json:"skipped,omitempty"` // files that could not carry the name (e.g. the customer's own template)
}

// OutputArtifact is one file on the outputs card.
type OutputArtifact struct {
	ID      string          `json:"id"`
	Name    string          `json:"name"`
	Format  string          `json:"format"`
	Size    int64           `json:"size"`
	Version int             `json:"version"`
	Quality json.RawMessage `json:"quality"`
	Meta    json.RawMessage `json:"meta,omitempty"`
}

// PostText adds an agent message to the thread.
func (s *Store) PostText(ctx context.Context, run Run, text string, extra map[string]any) error {
	p := map[string]any{"text": text, "run_id": run.ID.String()}
	for k, v := range extra {
		p[k] = v
	}
	rid := run.ID
	_, _, err := s.AddItem(ctx, s.Pool, run.InstallID, run.TenantID, run.UserID, &rid, "agent_text", p, nil)
	return err
}

// PostNotice adds a one-line notice (e.g. "remembered as a default").
func (s *Store) PostNotice(ctx context.Context, run Run, text, link string) error {
	rid := run.ID
	_, _, err := s.AddItem(ctx, s.Pool, run.InstallID, run.TenantID, run.UserID, &rid, "notice",
		map[string]any{"text": text, "link": link, "run_id": run.ID.String()}, nil)
	return err
}

// CardItemPayload renders a card row as its thread payload.
func CardItemPayload(c Card, intro, note string) CardPayload {
	p := CardPayload{CardID: c.ID.String(), RunID: c.RunID.String(), Kind: c.Kind, Moment: c.Moment, Blocking: c.Blocking,
		Status: c.Status, Intro: intro, Questions: c.Questions, Answer: c.Answer, Attempts: c.Attempts, Note: note}
	if c.AnsweredVia != nil {
		p.AnsweredVia = *c.AnsweredVia
	}
	if c.Supersedes != nil {
		p.Supersedes = c.Supersedes.String()
	}
	return p
}

// SyncCardItem updates a card's thread item after its row changed.
func (s *Store) SyncCardItem(ctx context.Context, q Querier, c Card) error {
	it, err := s.ItemFor(ctx, c.InstallID, "card", "card_id", c.ID.String())
	if err != nil {
		return err
	}
	patch := map[string]any{"status": c.Status, "attempts": c.Attempts}
	if len(c.Answer) > 0 {
		patch["answer"] = c.Answer
	}
	if c.AnsweredVia != nil {
		patch["answered_via"] = *c.AnsweredVia
	}
	return s.PatchItem(ctx, q, it.ID, patch)
}

// NewID is uuid.New, exported for handlers building keys before insert.
func NewID() uuid.UUID { return uuid.New() }
