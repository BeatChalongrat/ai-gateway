package agentmarket

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// Run states (docs: §0b). awaiting_input is a normal, indefinitely durable
// state — a run waiting on a person is not a failure and nothing times it out.
const (
	RunQueued        = "queued"
	RunRunning       = "running"
	RunAwaitingInput = "awaiting_input"
	RunSucceeded     = "succeeded"
	RunFailed        = "failed"
	RunCancelled     = "cancelled"
)

// Run kinds.
const (
	KindRun            = "run"
	KindSelfTest       = "self_test"
	KindRender         = "render"
	KindReply          = "reply"
	KindMappingPreview = "mapping_preview"
)

// IsTerminal reports a final run state.
func IsTerminal(status string) bool {
	return status == RunSucceeded || status == RunFailed || status == RunCancelled
}

// Definition is an installable package version.
type Definition struct {
	ID             uuid.UUID       `json:"id"`
	Slug           string          `json:"slug"`
	Version        string          `json:"version"`
	Name           string          `json:"name"`
	Manifest       json.RawMessage `json:"manifest"`
	Instructions   string          `json:"-"`
	Checksum       string          `json:"checksum"`
	EngineChecksum string          `json:"engine_checksum"`
	Status         string          `json:"status"`
	UpdatedAt      time.Time       `json:"updated_at"`
}

// Install is one user's installed agent.
type Install struct {
	ID                 uuid.UUID       `json:"id"`
	TenantID           uuid.UUID       `json:"-"`
	UserID             uuid.UUID       `json:"-"`
	DefinitionID       uuid.UUID       `json:"definition_id"`
	Slug               string          `json:"slug"`
	DefinitionVersion  string          `json:"definition_version"`
	Checksum           string          `json:"checksum"`
	Title              string          `json:"title"`
	CapabilityBindings json.RawMessage `json:"capability_bindings"`
	BindingVersion     int             `json:"binding_version"`
	BudgetCredits      float64         `json:"budget_credits"`
	Status             string          `json:"status"`
	SelfTestRunID      *uuid.UUID      `json:"self_test_run_id,omitempty"`
	CreatedAt          time.Time       `json:"created_at"`
	UpdatedAt          time.Time       `json:"updated_at"`
	// PreviousDefinitionID is the version an upgrade replaced (AM2): a
	// rollback points the install back at it.
	PreviousDefinitionID *uuid.UUID `json:"previous_definition_id,omitempty"`
}

// Default is a remembered answer or an install-time setting.
type Default struct {
	Key        string          `json:"key"`
	Value      json.RawMessage `json:"value"`
	Source     string          `json:"source"`
	Label      string          `json:"label"`
	Note       string          `json:"note"`
	FromRunID  *uuid.UUID      `json:"from_run_id,omitempty"`
	FromCardID *uuid.UUID      `json:"from_card_id,omitempty"`
	UpdatedAt  time.Time       `json:"updated_at"`
}

// Run is a job: a full run, a self-test, or a small job hanging off a run.
type Run struct {
	ID                uuid.UUID       `json:"id"`
	InstallID         uuid.UUID       `json:"install_id"`
	ParentRunID       *uuid.UUID      `json:"parent_run_id,omitempty"`
	TenantID          uuid.UUID       `json:"-"`
	UserID            uuid.UUID       `json:"-"`
	Kind              string          `json:"kind"`
	Status            string          `json:"status"`
	StatusReason      string          `json:"status_reason"`
	Period            *string         `json:"period"`
	Message           string          `json:"message"`
	Params            json.RawMessage `json:"params"`
	DefinitionVersion string          `json:"definition_version"`
	Checksum          string          `json:"checksum"`
	EngineChecksum    string          `json:"engine_checksum"`
	BindingVersion    int             `json:"binding_version"`
	Formats           json.RawMessage `json:"formats"`
	Answers           json.RawMessage `json:"answers"`
	Assumptions       json.RawMessage `json:"assumptions"`
	CancelRequested   bool            `json:"cancel_requested"`
	LeaseOwner        *string         `json:"-"`
	LeaseUntil        *time.Time      `json:"-"`
	Attempts          int             `json:"attempts"`
	CreditsSpent      float64         `json:"credits_spent"`
	ModelUsed         *string         `json:"model_used"`
	ModelSelfHosted   *bool           `json:"model_self_hosted"`
	AwaitingSince     *time.Time      `json:"awaiting_since"`
	AwaitingMS        int64           `json:"awaiting_ms"`
	CreatedAt         time.Time       `json:"created_at"`
	StartedAt         *time.Time      `json:"started_at"`
	FinishedAt        *time.Time      `json:"finished_at"`
	UpdatedAt         time.Time       `json:"updated_at"`
}

// Step is one row of the run's audit trail.
type Step struct {
	ID             uuid.UUID       `json:"id"`
	RunID          uuid.UUID       `json:"run_id"`
	Seq            int             `json:"seq"`
	Name           string          `json:"name"`
	Kind           string          `json:"kind"`
	Status         string          `json:"status"`
	IdempotencyKey string          `json:"idempotency_key"`
	Input          json.RawMessage `json:"input"`
	Output         json.RawMessage `json:"output"`
	ScriptID       string          `json:"script_id"`
	ModelID        string          `json:"model_id"`
	Error          string          `json:"error"`
	Attempt        int             `json:"attempt"`
	Credits        float64         `json:"credits"`
	StartedAt      time.Time       `json:"started_at"`
	FinishedAt     *time.Time      `json:"finished_at"`
	DurationMS     *int64          `json:"duration_ms"`
}

// File is an uploaded input.
type File struct {
	ID          uuid.UUID  `json:"id"`
	InstallID   uuid.UUID  `json:"install_id"`
	RunID       *uuid.UUID `json:"run_id,omitempty"`
	TenantID    uuid.UUID  `json:"-"`
	UserID      uuid.UUID  `json:"-"`
	Name        string     `json:"name"`
	ContentType string     `json:"content_type"`
	Size        int64      `json:"size"`
	SHA256      string     `json:"sha256"`
	StorageKey  string     `json:"-"`
	Status      string     `json:"status"`
	ClientID    *string    `json:"client_id,omitempty"`
	CreatedAt   time.Time  `json:"created_at"`
}

// Card is a clarification card (question card / request-files card / …).
type Card struct {
	ID          uuid.UUID       `json:"id"`
	RunID       uuid.UUID       `json:"run_id"`
	InstallID   uuid.UUID       `json:"install_id"`
	TenantID    uuid.UUID       `json:"-"`
	UserID      uuid.UUID       `json:"-"`
	Moment      string          `json:"moment"`
	Seq         int             `json:"seq"`
	Kind        string          `json:"kind"`
	Blocking    bool            `json:"blocking"`
	Supersedes  *uuid.UUID      `json:"supersedes,omitempty"`
	Questions   json.RawMessage `json:"questions"`
	Status      string          `json:"status"`
	Answer      json.RawMessage `json:"answer,omitempty"`
	AnswerText  *string         `json:"answer_text,omitempty"`
	AnsweredVia *string         `json:"answered_via,omitempty"`
	AnsweredAt  *time.Time      `json:"answered_at,omitempty"`
	Attempts    int             `json:"attempts"`
	LastError   string          `json:"last_error"`
	CreatedAt   time.Time       `json:"created_at"`
}

// Artifact is a delivered file version.
type Artifact struct {
	ID             uuid.UUID       `json:"id"`
	RunID          uuid.UUID       `json:"run_id"`
	InstallID      uuid.UUID       `json:"install_id"`
	Format         string          `json:"format"`
	Kind           string          `json:"kind"`
	Name           string          `json:"name"`
	Version        int             `json:"version"`
	StorageKey     string          `json:"-"`
	Size           int64           `json:"size"`
	SHA256         string          `json:"sha256"`
	ThemeID        string          `json:"theme_id"`
	ThemeVersion   int             `json:"theme_version"`
	Citations      json.RawMessage `json:"citations"`
	Validation     json.RawMessage `json:"validation"`
	ChartBindings  json.RawMessage `json:"chart_bindings"`
	ProducedByStep string          `json:"produced_by_step"`
	CreatedAt      time.Time       `json:"created_at"`
}

// ThreadItem is one entry of the agent workspace.
type ThreadItem struct {
	ID        uuid.UUID       `json:"id"`
	InstallID uuid.UUID       `json:"install_id"`
	RunID     *uuid.UUID      `json:"run_id,omitempty"`
	Kind      string          `json:"kind"`
	Payload   json.RawMessage `json:"payload"`
	ClientID  *string         `json:"client_id,omitempty"`
	Rev       int64           `json:"rev"`
	CreatedAt time.Time       `json:"created_at"`
	UpdatedAt time.Time       `json:"updated_at"`
}

// Question is one question inside a card (the shape the web QuestionCard renders).
type Question struct {
	Key          string          `json:"key"`
	Code         string          `json:"code"`
	Severity     string          `json:"severity"`
	Header       string          `json:"header"`
	Question     string          `json:"question"`
	MultiSelect  bool            `json:"multiSelect"`
	Options      []Option        `json:"options"`
	Default      json.RawMessage `json:"default"`
	DefaultLabel string          `json:"default_label"`
	Evidence     json.RawMessage `json:"evidence,omitempty"`
	Rememberable bool            `json:"rememberable"`
	Request      json.RawMessage `json:"request,omitempty"`
	// Input (AM2): the question also takes a typed value of this kind
	// ({"kind": "money"|"tax_id"|"date"|"text", "hint": "…"}). Text typed in
	// the card's own box is the answer as {"typed": text}; the package that
	// asked validates it deterministically — never a model's reading of it.
	Input json.RawMessage `json:"input,omitempty"`
}

// Option is one clickable answer. Value is what the check acts on.
type Option struct {
	Label       string          `json:"label"`
	Description string          `json:"description"`
	Value       json.RawMessage `json:"value"`
}

// Answer is a resolved answer, stored on the run keyed by finding key.
type Answer struct {
	Value  json.RawMessage `json:"value"`
	Label  string          `json:"label"`
	Moment string          `json:"moment"`
	CardID string          `json:"card_id,omitempty"`
	Via    string          `json:"via"` // card | text | default | reprompt_default | remembered
	// Cosmetic answers change presentation only: they re-render, never recompute.
	Cosmetic bool   `json:"cosmetic,omitempty"`
	Code     string `json:"code,omitempty"`
}

// Assumption is a line the report prints under "ข้อสมมติที่ใช้ในรอบนี้".
type Assumption struct {
	Code           string `json:"code"`
	Source         string `json:"source"` // default | remembered | answer | reprompt_default | data | rule
	Text           string `json:"text"`
	ChangesNumbers bool   `json:"changes_numbers,omitempty"`
}
