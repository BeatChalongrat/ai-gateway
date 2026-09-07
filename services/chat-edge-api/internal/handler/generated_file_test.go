package handler

// FIX81 — the one client door for assistant-produced files. Pins: bytes
// persist through SaveGeneratedFile with attribution; ownership is gated
// exactly like the upload path (404, nothing persisted); garbage is
// refused before anything is written.

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type fix81Store struct {
	ownedConv uuid.UUID
	saved     []service.GeneratedFileInput
}

func (f *fix81Store) AssertConversationOwned(_ context.Context, _, _, convID uuid.UUID) error {
	if convID != f.ownedConv {
		return service.ErrFileConvNotFound
	}
	return nil
}
func (f *fix81Store) SaveGeneratedFile(_ context.Context, in service.GeneratedFileInput) (*model.ConversationFile, error) {
	f.saved = append(f.saved, in)
	return &model.ConversationFile{ID: uuid.New(), ConversationID: in.ConversationID, OriginalFilename: in.OriginalFilename, ContentType: in.ContentType, FileSizeBytes: int64(len(in.Data)), IsGenerated: true}, nil
}

func fix81Post(t *testing.T, store *fix81Store, convID, body string) *httptest.ResponseRecorder {
	t.Helper()
	h := NewGeneratedFileHandler(store)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/conversations/"+convID+"/files/generated", strings.NewReader(body))
	ctx := chi.NewRouteContext()
	ctx.URLParams.Add("id", convID)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, ctx))
	rec := httptest.NewRecorder()
	h.Save(rec, req)
	return rec
}

func TestGeneratedFile_PersistsWithAttribution(t *testing.T) {
	conv := uuid.New()
	store := &fix81Store{ownedConv: conv}
	b64 := base64.StdEncoding.EncodeToString([]byte("PNGBYTES"))
	rec := fix81Post(t, store, conv.String(), `{"filename":"sunset.png","content_type":"image/png","data_base64":"`+b64+`","generated_by":"gpt-image-1"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, body %s", rec.Code, rec.Body.String())
	}
	if len(store.saved) != 1 {
		t.Fatalf("expected 1 save, got %d", len(store.saved))
	}
	in := store.saved[0]
	if string(in.Data) != "PNGBYTES" || in.OriginalFilename != "sunset.png" || in.GeneratedBy != "gpt-image-1" || in.ConversationID != conv {
		t.Errorf("bad save input: %+v", in)
	}
	var resp struct {
		ID          string `json:"id"`
		IsGenerated bool   `json:"is_generated"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil || resp.ID == "" || !resp.IsGenerated {
		t.Errorf("response must carry the file row (id, is_generated): %s", rec.Body.String())
	}
}

func TestGeneratedFile_ForeignConversationRefusedNothingPersisted(t *testing.T) {
	store := &fix81Store{ownedConv: uuid.New()}
	b64 := base64.StdEncoding.EncodeToString([]byte("X"))
	rec := fix81Post(t, store, uuid.New().String(), `{"filename":"a.png","content_type":"image/png","data_base64":"`+b64+`"}`)
	if rec.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", rec.Code)
	}
	if len(store.saved) != 0 {
		t.Fatalf("a refused conversation must persist nothing")
	}
}

func TestGeneratedFile_GarbageRefused(t *testing.T) {
	conv := uuid.New()
	store := &fix81Store{ownedConv: conv}
	for name, body := range map[string]string{
		"empty filename": `{"filename":"  ","content_type":"image/png","data_base64":"QQ=="}`,
		"bad base64":     `{"filename":"a.png","content_type":"image/png","data_base64":"@@@"}`,
		"empty data":     `{"filename":"a.png","content_type":"image/png","data_base64":""}`,
	} {
		rec := fix81Post(t, store, conv.String(), body)
		if rec.Code != http.StatusUnprocessableEntity {
			t.Errorf("%s: status = %d, want 422", name, rec.Code)
		}
	}
	if len(store.saved) != 0 {
		t.Fatalf("garbage must persist nothing")
	}
}
