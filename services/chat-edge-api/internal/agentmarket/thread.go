package agentmarket

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// Thread payloads — the shapes the web workspace renders. Kept in one
// place so both planes write exactly what the UI reads.
//
// AM6: a thread item is written as one of these shapes and as nothing else
// (ItemPayload), and every text of the agent's side in them is a PersonText —
// a string, an error's text included, does not fit. What is sent of each
// shape is declared in schema.go.

// ItemPayload is what a thread item carries: one of the shapes of this file.
type ItemPayload interface{ itemKind() string }

func (Greeting) itemKind() string       { return "greeting" }
func (AgentText) itemKind() string      { return "agent_text" }
func (Notice) itemKind() string         { return "notice" }
func (CardPayload) itemKind() string    { return "card" }
func (Progress) itemKind() string       { return "progress" }
func (OutputsPayload) itemKind() string { return "outputs" }
func (IntentPayload) itemKind() string  { return "intent" }
func (m UserMessage) itemKind() string {
	if len(m.Files) > 0 {
		return "user_files"
	}
	return "user_text"
}

// Greeting states the agent's job and the files it needs.
type Greeting struct {
	Text     PersonText   `json:"text"`
	Role     PersonText   `json:"role"`
	Tagline  PersonText   `json:"tagline"`
	Needs    []GreetNeed  `json:"needs"`
	Produces []GreetOut   `json:"produces"`
	Cannot   PersonText   `json:"cannot"`
	Icon     string       `json:"icon"`
	Slug     string       `json:"slug"`
	Version  string       `json:"version"`
	Extra    *GreetStatus `json:"status,omitempty"`
}

// GreetNeed is one input the agent asks for.
type GreetNeed struct {
	Role     string     `json:"role"`
	Label    PersonText `json:"label"`
	Note     PersonText `json:"note"`
	Required bool       `json:"required"`
}

// GreetOut is one output format.
type GreetOut struct {
	Format string     `json:"format"`
	Label  PersonText `json:"label"`
}

// GreetStatus carries install-time facts (e.g. the self-test run).
type GreetStatus struct {
	SelfTestRunID string `json:"self_test_run_id,omitempty"`
}

// GreetingFor builds the greeting from the manifest: the package's own words,
// as a person is shown them.
func GreetingFor(m Manifest) Greeting {
	g := Greeting{Text: FromPackage(m.Greeting.Text, NoText), Role: LabelFromPackage(m.Greeting.Role, NoText),
		Tagline: FromPackage(m.Greeting.Tagline, NoText), Cannot: FromPackage(m.Cannot, NoText),
		Icon: m.Icon, Slug: m.ID, Version: m.Version}
	for _, in := range m.Inputs {
		g.Needs = append(g.Needs, GreetNeed{Role: in.Role, Label: LabelFromPackage(in.Label, Text("ไฟล์ที่ agent ต้องใช้")),
			Note: FromPackage(in.Note, NoText), Required: in.Required})
	}
	for _, o := range m.Outputs {
		g.Produces = append(g.Produces, GreetOut{Format: o.Format, Label: LabelFromPackage(o.Label, NoText)})
	}
	return g
}

// FileRef names a file of the person's on a thread item.
type FileRef struct {
	ID   uuid.UUID `json:"id"`
	Name string    `json:"name"`
	Size int64     `json:"size"`
}

// FileRefs names files on a thread item.
func FileRefs(files []File) []FileRef {
	out := make([]FileRef, 0, len(files))
	for _, f := range files {
		out = append(out, FileRef{ID: f.ID, Name: f.Name, Size: f.Size})
	}
	return out
}

// UserMessage is what the person typed and sent: theirs, kept as it is.
type UserMessage struct {
	Text   string    `json:"text"`
	Status string    `json:"status"`
	Files  []FileRef `json:"files,omitempty"`
}

// AgentText is what the agent says in the thread.
type AgentText struct {
	Text  PersonText `json:"text"`
	RunID string     `json:"run_id,omitempty"`
	// Tone: "" | error (a failed round, with Failure) | open (a round that
	// finished with its gate open, with Completion) | warning
	Tone       string      `json:"tone,omitempty"`
	Failure    *Failure    `json:"failure,omitempty"`
	Completion *Completion `json:"completion,omitempty"`
	ReviewID   string      `json:"review_id,omitempty"`
	// Knowledge (AM6): the answer drew on the person's knowledge base — it is
	// not part of the round's files.
	Knowledge *AnswerKnowledge `json:"knowledge,omitempty"`
}

// Notice is a one-line notice.
type Notice struct {
	Text       PersonText `json:"text"`
	Link       string     `json:"link,omitempty"`
	RunID      string     `json:"run_id,omitempty"`
	Tone       string     `json:"tone,omitempty"`
	Action     string     `json:"action,omitempty"`
	Reason     string     `json:"reason,omitempty"`
	Contact    *Contact   `json:"contact,omitempty"`
	RoundStart bool       `json:"round_start,omitempty"`
	Access     bool       `json:"access,omitempty"`
	RetryOf    string     `json:"retry_of,omitempty"`
	Restored   string     `json:"restored,omitempty"`
	ShareID    string     `json:"share_id,omitempty"`
	ReviewID   string     `json:"review_id,omitempty"`
}

// CardPayload is a card as the thread shows it. Its questions are a
// package's (or the platform's) findings, kept as they were asked.
type CardPayload struct {
	CardID      string          `json:"card_id"`
	RunID       string          `json:"run_id"`
	Kind        string          `json:"kind"`
	Moment      string          `json:"moment"`
	Blocking    bool            `json:"blocking"`
	Status      string          `json:"status"`
	Intro       PersonText      `json:"intro,omitzero"`
	Questions   json.RawMessage `json:"questions"`
	Answer      json.RawMessage `json:"answer,omitempty"`
	AnsweredVia string          `json:"answered_via,omitempty"`
	Attempts    int             `json:"attempts"`
	Note        PersonText      `json:"note,omitzero"`
	Supersedes  string          `json:"supersedes,omitempty"`
	Files       []FileRef       `json:"files,omitempty"` // late_file: the files in question
}

// ProgressStep is one line of the inline progress card.
type ProgressStep struct {
	Key   string     `json:"key"`
	Label PersonText `json:"label"`
	// Status: pending | running | done | waiting | failed | skipped | open
	// (AM6: a gate that was checked and did not pass — not a failure)
	Status string     `json:"status"`
	Detail PersonText `json:"detail,omitzero"`
}

// Progress is the inline progress card of a run.
type Progress struct {
	RunID  string `json:"run_id"`
	Kind   string `json:"kind"`
	Status string `json:"status"`
	// Outcome (AM6) is what the round came to: see Run.Outcome.
	Outcome    string         `json:"outcome,omitempty"`
	Reason     string         `json:"reason,omitempty"`
	StartedAt  *time.Time     `json:"started_at,omitempty"`
	FinishedAt *time.Time     `json:"finished_at,omitempty"`
	ElapsedMS  int64          `json:"elapsed_ms"`
	AwaitingMS int64          `json:"awaiting_ms"`
	Steps      []ProgressStep `json:"steps"`
}

// QueuedProgress is the progress card of a run that has not started.
func QueuedProgress(run Run) Progress {
	return Progress{RunID: run.ID.String(), Kind: run.Kind, Status: RunQueued, Outcome: RunQueued}
}

// OutputsPayload is the delivered-files card.
type OutputsPayload struct {
	RunID       string           `json:"run_id"`
	PeriodLabel PersonText       `json:"period_label"`
	Artifacts   []OutputArtifact `json:"artifacts"`
	// Summary is the package's own summary of its round (schema.go declares
	// which of its fields are sent, and how).
	Summary  map[string]any `json:"summary,omitempty"`
	Model    *OutputsModel  `json:"model,omitempty"`
	Credits  float64        `json:"credits,omitempty"` // AM11b: no longer written; cards from before carry it and no client shows it
	Rendered bool           `json:"rerender,omitempty"`
	// Review (AM3): these files carry a reviewer's sign-off on their face.
	Review *OutputsReview `json:"review,omitempty"`
	// Draft (AM5): the package's completion gate did not pass — the files are
	// delivered for the person to see why, and say "draft" on their face.
	// Gate is what the gate said, in the package's words.
	Draft bool         `json:"draft,omitempty"`
	Gate  *OutputsGate `json:"gate,omitempty"`
	// MoreFormats (AM8) are the formats this agent can still make from the round's numbers — its
	// manifest's outputs, PDF only while a converter is there: the card offers these and no others.
	MoreFormats []string `json:"more_formats,omitempty"`
}

// OutputsModel is what the files card says about models (AM4).
type OutputsModel struct {
	Model            string `json:"model"`
	SelfHosted       bool   `json:"self_hosted"`
	Choice           string `json:"choice,omitempty"`
	Uses             string `json:"uses,omitempty"`
	ChosenModel      string `json:"chosen_model,omitempty"`
	VisionModel      string `json:"vision_model,omitempty"`
	VisionSelfHosted *bool  `json:"vision_self_hosted,omitempty"`
	NotChosen        *bool  `json:"not_chosen,omitempty"`
	WrittenByRules   *bool  `json:"written_by_rules,omitempty"`
	// Web (AM12, Uses "web"): the models that read the request and the saved pages in a round on the
	// web, in the order they first answered. Empty: no model answered (a sample round reads none).
	Web []OutputsWebModel `json:"web,omitempty"`
}

// OutputsWebModel is one model that read in a round on the web.
type OutputsWebModel struct {
	Model      string `json:"model"`
	SelfHosted bool   `json:"self_hosted"`
}

// OutputsGate is a package's completion gate as the files card shows it.
type OutputsGate struct {
	Passed bool       `json:"passed"`
	What   PersonText `json:"what"`
	Next   PersonText `json:"next,omitzero"`
}

// OutputsReview is the sign-off an outputs card was re-issued with.
type OutputsReview struct {
	ID       string       `json:"id"`
	Reviewer string       `json:"reviewer"` // the reviewer's own name
	AtLabel  PersonText   `json:"at_label"`
	Note     string       `json:"note,omitempty"`    // the reviewer's own words
	Skipped  []PersonText `json:"skipped,omitempty"` // files that could not carry the name (e.g. the customer's own template)
}

// OutputArtifact is one file on the outputs card.
type OutputArtifact struct {
	ID      string          `json:"id"`
	Name    string          `json:"name"`
	Format  string          `json:"format"`
	Size    int64           `json:"size"`
	Version int             `json:"version"`
	Quality json.RawMessage `json:"quality"`
}

// PostText adds an agent message to the thread.
func (s *Store) PostText(ctx context.Context, run Run, text PersonText) error {
	return s.PostAgentText(ctx, run, AgentText{Text: text})
}

// PostAgentText adds an agent message with its tone and what it carries.
// AM11: what is said in a run's thread about the run is in the run's own
// language, whoever writes it (InRunLang).
func (s *Store) PostAgentText(ctx context.Context, run Run, item AgentText) error {
	item.RunID = run.ID.String()
	rid := run.ID
	_, _, err := s.AddItem(InRunLang(ctx, run), s.Pool, run.InstallID, run.TenantID, run.UserID, &rid, item, nil)
	return err
}

// PostNotice adds a one-line notice (e.g. "remembered as a default").
func (s *Store) PostNotice(ctx context.Context, run Run, text PersonText, link string) error {
	return s.AddNotice(ctx, s.Pool, run, Notice{Text: text, Link: link})
}

// AddNotice adds a notice about a run, in the run's own language (AM11).
func (s *Store) AddNotice(ctx context.Context, q Querier, run Run, n Notice) error {
	n.RunID = run.ID.String()
	rid := run.ID
	_, _, err := s.AddItem(InRunLang(ctx, run), q, run.InstallID, run.TenantID, run.UserID, &rid, n, nil)
	return err
}

// CardItemPayload renders a card row as its thread payload.
func CardItemPayload(c Card, intro, note PersonText) CardPayload {
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

// ItemPatch is what may change on an item after it was written. Like the
// shapes above it takes no string where a person reads.
type ItemPatch struct {
	Status      string          `json:"status,omitempty"`
	Outcome     string          `json:"outcome,omitempty"`
	Attempts    *int            `json:"attempts,omitempty"`
	Answer      json.RawMessage `json:"answer,omitempty"`
	AnsweredVia string          `json:"answered_via,omitempty"`
	HandledAs   string          `json:"handled_as,omitempty"`
	Choice      string          `json:"choice,omitempty"`
	RunID       string          `json:"run_id,omitempty"`
	Note        *PersonText     `json:"note,omitempty"`
}

// SyncCardItem updates a card's thread item after its row changed.
func (s *Store) SyncCardItem(ctx context.Context, q Querier, c Card) error {
	it, err := s.ItemFor(ctx, c.InstallID, "card", "card_id", c.ID.String())
	if err != nil {
		return err
	}
	attempts := c.Attempts
	patch := ItemPatch{Status: c.Status, Attempts: &attempts}
	if len(c.Answer) > 0 {
		patch.Answer = c.Answer
	}
	if c.AnsweredVia != nil {
		patch.AnsweredVia = *c.AnsweredVia
	}
	return s.PatchItem(ctx, q, it.ID, patch)
}

// NewID is uuid.New, exported for handlers building keys before insert.
func NewID() uuid.UUID { return uuid.New() }
