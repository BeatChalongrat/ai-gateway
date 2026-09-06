package service

// FIX11 §4 — tests for the two image defects:
//   4.1  an attached image that fails to encode must reach the USER as a
//        notice, not vanish into a log line.
//   4.2  each user message that carries images keeps its OWN inline image
//        blocks; an image attached on an earlier turn survives later text
//        turns instead of being dropped or re-homed onto the last message.

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// fakeFileLinker is a tiny in-package FileLinker so resolveUserImages can
// be exercised without the real FileService / sandbox / storage.
type fakeFileLinker struct {
	urls  map[uuid.UUID][]string
	fails map[uuid.UUID][]ImageAttachFailure
}

// vis wraps bare URLs the way the file service now hands them over.
func vis(urls ...string) []VisionImage {
	out := make([]VisionImage, len(urls))
	for i, u := range urls {
		out[i] = VisionImage{URL: u, Filename: "img.png"}
	}
	return out
}

func (f *fakeFileLinker) AttachToMessage(
	context.Context, uuid.UUID, uuid.UUID, uuid.UUID, []uuid.UUID,
) (int64, error) {
	return 0, nil
}
func (f *fakeFileLinker) BuildContextBlock(context.Context, uuid.UUID, uuid.UUID) (string, error) {
	return "", nil
}
func (f *fakeFileLinker) ImageDataURLsForMessage(_ context.Context, _, m uuid.UUID) []string {
	return f.urls[m]
}
func (f *fakeFileLinker) ImageDataURLsForMessageChecked(
	_ context.Context, _, m uuid.UUID,
) ([]VisionImage, []ImageAttachFailure) {
	return vis(f.urls[m]...), f.fails[m]
}
func (f *fakeFileLinker) GeneratedImageNotes(
	context.Context, uuid.UUID, []uuid.UUID,
) map[uuid.UUID]string {
	return nil
}
func (f *fakeFileLinker) GeneratedImageVisionURLs(
	context.Context, uuid.UUID, []uuid.UUID,
) map[uuid.UUID][]VisionImage {
	return nil
}

// 4.1 — a failed encode on the CURRENT turn surfaces as a failure the send
// path can turn into a notice, and does NOT silently drop earlier images.
func TestResolveUserImages_SurfacesCurrentTurnFailure(t *testing.T) {
	u1 := uuid.New() // earlier user turn, image OK
	u3 := uuid.New() // current user turn, image FAILS
	history := []model.Message{
		{ID: u1, Role: "user", ContentMasked: "look at this"},
		{ID: uuid.New(), Role: "assistant", ContentMasked: "ok"},
		{ID: uuid.New(), Role: "user", ContentMasked: "a text turn"},
		{ID: u3, Role: "user", ContentMasked: "and this one?"},
	}
	fake := &fakeFileLinker{
		urls: map[uuid.UUID][]string{u1: {"data:image/png;base64,AAAA"}},
		fails: map[uuid.UUID][]ImageAttachFailure{
			u3: {{Filename: "broken.png", Reason: "unreadable"}},
		},
	}
	s := &MessageService{fileSvc: fake}

	set := s.resolveUserImages(context.Background(), uuid.New(), history)

	if len(set.failures) != 1 || set.failures[0].Filename != "broken.png" {
		t.Fatalf("current-turn failure not surfaced: %+v", set.failures)
	}
	// The earlier turn's good image must still be in the map — not dropped.
	if got := set.byMsg[u1]; len(got) != 1 || got[0].URL != "data:image/png;base64,AAAA" {
		t.Fatalf("earlier image dropped: %+v", set.byMsg)
	}
}

// 4.1 — emitImageNotice puts exactly one ImageNotice frame on the stream so
// the drain forwards it to the client; empty failures produce no frame.
func TestEmitImageNotice(t *testing.T) {
	ch := make(chan StreamUpdate, 4)
	emitImageNotice(ch, []ImageAttachFailure{{Filename: "x.png", Reason: "unreadable"}})
	select {
	case ev := <-ch:
		if ev.ImageNotice == nil || len(ev.ImageNotice.Failures) != 1 ||
			ev.ImageNotice.Failures[0].Filename != "x.png" {
			t.Fatalf("notice frame malformed: %+v", ev)
		}
	default:
		t.Fatal("expected an image_notice frame, channel was empty")
	}

	emitImageNotice(ch, nil)
	select {
	case ev := <-ch:
		t.Fatalf("no failures should emit nothing, got %+v", ev)
	default:
	}
}

// 4.2 — the classic scenario: send an image, send two text turns, then ask
// about the image. The earlier turn's image must still be inline (image
// BEFORE text) in the built request, and the later text turns stay strings.
func TestBuildGatewayRequest_EarlierImageSurvivesLaterTextTurns(t *testing.T) {
	u1 := uuid.New()
	history := []model.Message{
		{ID: u1, Role: "user", ContentMasked: "see this"},
		{ID: uuid.New(), Role: "assistant", ContentMasked: "ok"},
		{ID: uuid.New(), Role: "user", ContentMasked: "another thing"},
		{ID: uuid.New(), Role: "user", ContentMasked: "what was in the first image?"},
	}
	imagesByMsg := map[uuid.UUID][]VisionImage{u1: vis("data:image/png;base64,IMG")}

	req := buildGatewayRequestWithImages("m", history, "", imagesByMsg, nil, nil, false)

	if len(req.Messages) != 4 {
		t.Fatalf("expected 4 messages, got %d", len(req.Messages))
	}
	// Message 0 (the image-bearing earlier turn) must be multimodal.
	blocks, ok := req.Messages[0].Content.([]GatewayContentBlock)
	if !ok {
		t.Fatalf("earlier user turn is %T, want []GatewayContentBlock", req.Messages[0].Content)
	}
	if len(blocks) != 3 {
		t.Fatalf("expected label + image + text blocks, got %d", len(blocks))
	}
	// FIX76 label, then image-before-text ordering (Anthropic guidance).
	if !isImageLabel(blocks[0]) {
		t.Errorf("block 0 must be the image label: %+v", blocks[0])
	}
	if blocks[1].Type != "image_url" || blocks[1].ImageURL == nil ||
		blocks[1].ImageURL.URL != "data:image/png;base64,IMG" {
		t.Errorf("block 1 must be the image: %+v", blocks[1])
	}
	if blocks[2].Type != "text" || blocks[2].Text != "see this" {
		t.Errorf("block 2 must be the message text: %+v", blocks[2])
	}
	// The final ("what was in the first image?") turn is a plain string —
	// proving the image did NOT re-home onto the last user message.
	if got, ok := req.Messages[3].Content.(string); !ok || got != "what was in the first image?" {
		t.Errorf("last user turn should stay text, got %T %v", req.Messages[3].Content, req.Messages[3].Content)
	}
}

// 4.2 — two separate user messages each carrying images each get their own
// inline blocks (not merged, not only-the-last).
func TestBuildGatewayRequest_EachImageMessageGetsOwnBlocks(t *testing.T) {
	u1, u2 := uuid.New(), uuid.New()
	history := []model.Message{
		{ID: u1, Role: "user", ContentMasked: "first"},
		{ID: uuid.New(), Role: "assistant", ContentMasked: "ok"},
		{ID: u2, Role: "user", ContentMasked: "second"},
	}
	imagesByMsg := map[uuid.UUID][]VisionImage{
		u1: vis("data:image/png;base64,ONE"),
		u2: vis("data:image/png;base64,TWO"),
	}

	req := buildGatewayRequestWithImages("m", history, "", imagesByMsg, nil, nil, false)

	for _, idx := range []int{0, 2} {
		if _, ok := req.Messages[idx].Content.([]GatewayContentBlock); !ok {
			t.Fatalf("message %d should be multimodal, got %T", idx, req.Messages[idx].Content)
		}
	}
}

// 4.2 — replayed history images respect the existing vision caps so
// re-sending old images can't blow the budget. The current turn is exempt.
func TestResolveUserImages_CapsReplayedHistory(t *testing.T) {
	fake := &fakeFileLinker{urls: map[uuid.UUID][]string{}}
	var history []model.Message
	// Build more historical image-bearing user turns than the cap allows,
	// each interleaved with an assistant turn.
	for i := 0; i < visionHistoryMaxImages+3; i++ {
		id := uuid.New()
		fake.urls[id] = []string{"data:image/png;base64,X"}
		history = append(history,
			model.Message{ID: id, Role: "user", ContentMasked: "img"},
			model.Message{ID: uuid.New(), Role: "assistant", ContentMasked: "ok"},
		)
	}
	// Final current turn: plain text, no image.
	history = append(history, model.Message{ID: uuid.New(), Role: "user", ContentMasked: "now?"})

	s := &MessageService{fileSvc: fake}
	set := s.resolveUserImages(context.Background(), uuid.New(), history)

	total := 0
	for _, urls := range set.byMsg {
		total += len(urls)
	}
	if total > visionHistoryMaxImages {
		t.Fatalf("replayed images %d exceed cap %d", total, visionHistoryMaxImages)
	}
}
