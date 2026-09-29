package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
)

// ── Knowledge in ask mode (AM6) ──────────────────────────────────────────
//
// The chart of accounts, the cost-centre list and the expense policy already
// live in the person's knowledge base (the chat's), and they are what someone
// wants when asking about a figure. They must never become the source of a
// number in a deliverable: that stays with the files and the scripts.
//
// So a knowledge base named with a message is read only when the message
// becomes a reply — a question the agent answers — and never when it starts
// a round, resumes one, adds a file to one or answers its card. What was found
// travels on the reply job's params and nowhere else: no run row, no step, no
// compute key holds it. The runner's reply job asks its model with the
// round's figures and the passages, and marks the answer as drawn from the
// knowledge base and not part of the report.
//
// Agent Market does not import the chat's services: chat-edge hands in a
// reader (SetKnowledge) around its own retrieval, which reads a knowledge base
// only for its owner. Without one, a question is answered as it always was.

// KnowledgeSource is one passage's file as the answer names it: its name, and
// which part of the file the passage is (from 1; 0 = not known).
type KnowledgeSource struct {
	Title string `json:"title"`
	Part  int    `json:"part,omitempty"`
}

// KnowledgeContext is what a question found in a knowledge base: the base's
// name, the passages as the model is given them, and their files.
type KnowledgeContext struct {
	Base    string            `json:"base"`
	Text    string            `json:"text"`
	Sources []KnowledgeSource `json:"sources"`
}

// Knowledge reads a person's knowledge base for a question, as that person:
// the passages as a block for a model, their files, and the base's name. A
// base that is not the person's reads nothing.
type Knowledge interface {
	Context(ctx context.Context, tenant, user, kb uuid.UUID, query string, maxTokens int) (text string, sources []KnowledgeSource, base string, err error)
}

// KnowledgeFunc is a function that reads a knowledge base.
type KnowledgeFunc func(ctx context.Context, tenant, user, kb uuid.UUID, query string, maxTokens int) (string, []KnowledgeSource, string, error)

// Context calls the function.
func (f KnowledgeFunc) Context(ctx context.Context, tenant, user, kb uuid.UUID, query string, maxTokens int) (string, []KnowledgeSource, string, error) {
	return f(ctx, tenant, user, kb, query, maxTokens)
}

// KnowledgeSourceFrom reads one passage as the chat's retrieval records it: its
// label, and its metadata's file name and place in the file (chunk_index, from 0).
func KnowledgeSourceFrom(label string, metadata json.RawMessage) KnowledgeSource {
	var m struct {
		Filename string `json:"filename"`
		Chunk    *int   `json:"chunk_index"`
	}
	_ = json.Unmarshal(metadata, &m)
	s := KnowledgeSource{Title: strings.TrimSpace(m.Filename)}
	if s.Title == "" {
		s.Title = strings.TrimSpace(label)
	}
	if m.Chunk != nil && *m.Chunk >= 0 {
		s.Part = *m.Chunk + 1
	}
	return s
}

// SetKnowledge gives the control plane the chat's knowledge-base reader.
func (a *API) SetKnowledge(k Knowledge) { a.knowledge = k }

const (
	// knowledgeTokens caps the passages handed to the agent's model.
	knowledgeTokens = 3000
	// knowledgeWait is as long as a message waits for its passages.
	knowledgeWait = 10 * time.Second
)

// knowledgeFor reads the knowledge base named with a question, as the
// signed-in person. nil: no reader, nothing found, or the base is not theirs —
// then the question is answered as it would be without one.
func (a *API) knowledgeFor(ctx context.Context, c middleware.Caller, kb uuid.UUID, question string) *KnowledgeContext {
	if a.knowledge == nil || kb == uuid.Nil || strings.TrimSpace(question) == "" {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, knowledgeWait)
	defer cancel()
	text, sources, base, err := a.knowledge.Context(ctx, c.TenantUUID, c.UserID, kb, question, knowledgeTokens)
	if err != nil {
		a.log.Warn("knowledge base not read; the question is answered without it", "err", err)
		return nil
	}
	if strings.TrimSpace(text) == "" || len(sources) == 0 {
		return nil
	}
	return &KnowledgeContext{Base: base, Text: text, Sources: sources}
}

// AnswerSource is one file an answer drew on, as the thread shows it.
type AnswerSource struct {
	Title   string     `json:"title"` // the file's name, the person's own
	Locator PersonText `json:"locator,omitzero"`
}

// AnswerKnowledge is what an answer says of the knowledge base it drew on.
// The passages themselves are not sent: the person has them.
type AnswerKnowledge struct {
	Base    string         `json:"base"` // the knowledge base's name, the person's own
	Sources []AnswerSource `json:"sources"`
}

// Shown is what an answer that drew on this knowledge says of it.
func (k *KnowledgeContext) Shown() *AnswerKnowledge {
	if k == nil {
		return nil
	}
	out := &AnswerKnowledge{Base: k.Base, Sources: []AnswerSource{}}
	for _, s := range k.Sources {
		src := AnswerSource{Title: s.Title}
		if s.Part > 0 {
			src.Locator = Text("ส่วนที่ %s", Count(s.Part))
		}
		out.Sources = append(out.Sources, src)
	}
	return out
}

// Titles are the files the passages came from, once each, as a sentence names them.
func (k *KnowledgeContext) Titles() Fact {
	var names []string
	seen := map[string]bool{}
	if k != nil {
		for _, s := range k.Sources {
			if s.Title != "" && !seen[s.Title] {
				seen[s.Title] = true
				names = append(names, s.Title)
			}
		}
	}
	return FileNames(names)
}
