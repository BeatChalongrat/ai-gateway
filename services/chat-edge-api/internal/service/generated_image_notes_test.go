package service

// Tests for the generated-image history annotation: an assistant turn
// that produced an AI image gets an API-side context note appended to
// the OUTBOUND message copy, so a model switched mid-conversation
// doesn't contradict the bare "image created" text. Stored history
// must never be mutated.

import (
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestGeneratedImageNote_Phrasing(t *testing.T) {
	one := generatedImageNote([]string{"สร้างรูปแมวที.png"})
	if !strings.Contains(one, "An AI-generated image") ||
		!strings.Contains(one, "สร้างรูปแมวที.png") {
		t.Errorf("single-image note malformed: %s", one)
	}
	many := generatedImageNote([]string{"a.png", "b.png"})
	if !strings.Contains(many, "AI-generated images") ||
		!strings.Contains(many, "a.png, b.png") {
		t.Errorf("multi-image note malformed: %s", many)
	}
}

func TestBuildGatewayRequest_AppendsGeneratedNoteToAssistantCopy(t *testing.T) {
	asstID := uuid.New()
	history := []model.Message{
		{ID: uuid.New(), Role: "user", ContentMasked: "สร้างรูปแมวที"},
		{ID: asstID, Role: "assistant", ContentMasked: "สร้างรูปเรียบร้อยแล้วครับ"},
		{ID: uuid.New(), Role: "user", ContentMasked: "สวยมั้ย"},
	}
	notes := map[uuid.UUID]string{
		asstID: generatedImageNote([]string{"สร้างรูปแมวที.png"}),
	}

	req := buildGatewayRequestWithImages("gpt-4-turbo", history, "sys", nil, notes, nil, false)

	// system + 3 history messages
	if len(req.Messages) != 4 {
		t.Fatalf("expected 4 messages, got %d", len(req.Messages))
	}
	asst, ok := req.Messages[2].Content.(string)
	if !ok {
		t.Fatalf("assistant content is not a string: %T", req.Messages[2].Content)
	}
	if !strings.HasPrefix(asst, "สร้างรูปเรียบร้อยแล้วครับ") {
		t.Errorf("assistant text lost: %s", asst)
	}
	if !strings.Contains(asst, "[Context: An AI-generated image") {
		t.Errorf("note not appended: %s", asst)
	}
	// User turns stay untouched even when the map is non-nil.
	if got := req.Messages[3].Content.(string); got != "สวยมั้ย" {
		t.Errorf("user turn modified: %s", got)
	}
	// The stored history slice must NOT be mutated — only the outbound
	// copy carries the note.
	if history[1].ContentMasked != "สร้างรูปเรียบร้อยแล้วครับ" {
		t.Errorf("stored history mutated: %s", history[1].ContentMasked)
	}
}

func TestBuildGatewayRequest_NoNotes_HistoryUnchanged(t *testing.T) {
	history := []model.Message{
		{ID: uuid.New(), Role: "user", ContentMasked: "hello"},
		{ID: uuid.New(), Role: "assistant", ContentMasked: "hi"},
	}
	req := buildGatewayRequestWithImages("gpt-4o", history, "", nil, nil, nil, false)
	if got := req.Messages[1].Content.(string); got != "hi" {
		t.Errorf("assistant content changed without notes: %s", got)
	}
}

func TestBuildGatewayRequest_ReplaysGeneratedImagesAsSyntheticUserTurn(t *testing.T) {
	asstID := uuid.New()
	history := []model.Message{
		{ID: uuid.New(), Role: "user", ContentMasked: "สร้างรูปแมวที"},
		{ID: asstID, Role: "assistant", ContentMasked: "สร้างรูปเรียบร้อยแล้วครับ"},
		{ID: uuid.New(), Role: "user", ContentMasked: "แมวในรูปสีอะไร"},
	}
	notes := map[uuid.UUID]string{asstID: generatedImageNote([]string{"cat.png"})}
	vision := map[uuid.UUID][]VisionImage{
		asstID: {{URL: "data:image/png;base64,AAAA", Filename: "cat.png"}},
	}

	req := buildGatewayRequestWithImages("gpt-4o", history, "sys", nil, notes, vision, false)

	// system, user, assistant, SYNTHETIC user (image), user
	if len(req.Messages) != 5 {
		t.Fatalf("expected 5 messages, got %d", len(req.Messages))
	}
	synth := req.Messages[3]
	if synth.Role != "user" {
		t.Fatalf("synthetic turn role = %s, want user", synth.Role)
	}
	blocks, ok := synth.Content.([]GatewayContentBlock)
	if !ok {
		t.Fatalf("synthetic content is %T, want []GatewayContentBlock", synth.Content)
	}
	if len(blocks) != 3 {
		t.Fatalf("expected label + image + text blocks, got %d", len(blocks))
	}
	if blocks[0].Type != "text" || blocks[0].Text != "Image 1 of 1: cat.png" {
		t.Errorf("FIX76 label malformed: %+v", blocks[0])
	}
	if blocks[1].Type != "image_url" || blocks[1].ImageURL == nil ||
		blocks[1].ImageURL.URL != "data:image/png;base64,AAAA" {
		t.Errorf("image block malformed: %+v", blocks[1])
	}
	if blocks[1].ImageURL.Detail != "low" {
		t.Errorf("detail = %s, want low", blocks[1].ImageURL.Detail)
	}
	if blocks[2].Type != "text" || !strings.Contains(blocks[2].Text, "Reference") {
		t.Errorf("text block malformed: %+v", blocks[2])
	}
	// The real final user turn must still be last and untouched.
	if got := req.Messages[4].Content.(string); got != "แมวในรูปสีอะไร" {
		t.Errorf("final user turn wrong: %s", got)
	}
	// Assistant message itself stays a plain string (note appended) —
	// no image blocks on assistant role.
	if _, isStr := req.Messages[2].Content.(string); !isStr {
		t.Errorf("assistant content must stay a string, got %T", req.Messages[2].Content)
	}
}
