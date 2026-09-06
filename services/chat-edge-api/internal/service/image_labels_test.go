package service

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// FIX76 — image labels: the wording, its placement, and the rule that a
// label never outlives its image. The desktop copy in
// apps/desktop/src/main/attachments.ts is the reference; the contract
// test at the bottom fails on any drift between the two.

func TestImageLabel_OneFixedForm(t *testing.T) {
	if got := imageLabel(2, 3, "design-ref.png"); got != "Image 2 of 3: design-ref.png" {
		t.Fatalf("label = %q", got)
	}
	if !imageLabelRe.MatchString("Image 2 of 3: design-ref.png") || imageLabelRe.MatchString("Image two: x.png") {
		t.Fatal("label regexp does not match the form it should")
	}
	if got := imageLabelName("a\nb\tc.png"); got != "a b c.png" {
		t.Errorf("control characters: %q", got)
	}
	if got := imageLabelName("   "); got != "image" {
		t.Errorf("empty name: %q", got)
	}
	long := strings.Repeat("ก", 200) + ".png"
	if n := []rune(imageLabelName(long)); len(n) != imageLabelMaxName || n[len(n)-1] != '…' {
		t.Errorf("long name not bounded: %d runes", len(n))
	}
}

func TestBuildGatewayRequest_LabelsEveryUserImage(t *testing.T) {
	u1 := uuid.New()
	history := []model.Message{{ID: u1, Role: "user", ContentMasked: "ใช้สีจากรูป 2"}}
	imgs := map[uuid.UUID][]VisionImage{u1: {
		{URL: "data:image/png;base64,A", Filename: "screenshot-old.png", Index: 1, Total: 2},
		{URL: "data:image/png;base64,B", Filename: "design-ref.png", Index: 2, Total: 2},
	}}
	req := buildGatewayRequestWithImages("m", history, "", imgs, nil, nil, false)
	blocks := req.Messages[0].Content.([]GatewayContentBlock)
	want := []string{"Image 1 of 2: screenshot-old.png", "<image>", "Image 2 of 2: design-ref.png", "<image>", "ใช้สีจากรูป 2"}
	if got := shape(blocks); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("blocks = %v, want %v", got, want)
	}
	if blocks[1].ImageURL.Detail != "auto" {
		t.Errorf("attached image detail = %q, want auto", blocks[1].ImageURL.Detail)
	}
}

func TestBuildGatewayRequest_CappedHistoryKeepsOriginalNumbers(t *testing.T) {
	// The resolver fixed Index/Total before the cap dropped the third image;
	// the survivors keep their numbers, so the user's "รูป 2" still holds.
	u1 := uuid.New()
	history := []model.Message{{ID: u1, Role: "user", ContentMasked: "x"}}
	imgs := map[uuid.UUID][]VisionImage{u1: {
		{URL: "data:1", Filename: "one.png", Index: 1, Total: 3},
		{URL: "data:2", Filename: "two.png", Index: 2, Total: 3},
	}}
	req := buildGatewayRequestWithImages("m", history, "", imgs, nil, nil, false)
	got := shape(req.Messages[0].Content.([]GatewayContentBlock))
	if got[0] != "Image 1 of 3: one.png" || got[2] != "Image 2 of 3: two.png" {
		t.Fatalf("numbers rewritten: %v", got)
	}
}

func TestStripImageBlocks_NoLabelOutlivesItsImage(t *testing.T) {
	u1 := uuid.New()
	history := []model.Message{{ID: u1, Role: "user", ContentMasked: "what colour?"}}
	imgs := map[uuid.UUID][]VisionImage{u1: {{URL: "data:a", Filename: "a.png"}, {URL: "data:b", Filename: "b.png"}}}
	req := buildGatewayRequestWithImages("m", history, "", imgs, nil, nil, false)
	if removed := stripImageBlocks(req.Messages); removed != 2 {
		t.Fatalf("removed = %d", removed)
	}
	text, ok := req.Messages[0].Content.(string)
	if !ok {
		t.Fatalf("content is %T", req.Messages[0].Content)
	}
	if strings.Contains(text, "a.png") || strings.Contains(text, "b.png") {
		t.Fatalf("a label survived the strip: %q", text)
	}
	for _, line := range strings.Split(text, "\n") {
		if imageLabelRe.MatchString(line) {
			t.Fatalf("label line survived: %q", line)
		}
	}
	if !strings.Contains(text, "what colour?") || strings.Count(text, imageNotSentNote) != 2 {
		t.Fatalf("user text or notes lost: %q", text)
	}
}

func TestBuildGatewayRequest_GeneratedImagesAreLabelledTheSameWay(t *testing.T) {
	asstID := uuid.New()
	history := []model.Message{
		{ID: uuid.New(), Role: "user", ContentMasked: "สร้างโลโก้ 2 แบบ"},
		{ID: asstID, Role: "assistant", ContentMasked: "done"},
		{ID: uuid.New(), Role: "user", ContentMasked: "เอาแบบรูป 2"},
	}
	vision := map[uuid.UUID][]VisionImage{asstID: {
		{URL: "data:g1", Filename: "logo-a.png", Index: 1, Total: 2},
		{URL: "data:g2", Filename: "logo-b.png", Index: 2, Total: 2},
	}}
	req := buildGatewayRequestWithImages("m", history, "", nil, map[uuid.UUID]string{asstID: "note"}, vision, false)
	synth := req.Messages[2].Content.([]GatewayContentBlock)
	got := shape(synth)
	if got[0] != "Image 1 of 2: logo-a.png" || got[1] != "<image>" || got[2] != "Image 2 of 2: logo-b.png" || got[3] != "<image>" || !strings.Contains(got[4], "Reference") {
		t.Fatalf("synthetic turn = %v", got)
	}
	if synth[1].ImageURL.Detail != "low" {
		t.Errorf("generated image detail = %q, want low", synth[1].ImageURL.Detail)
	}
}

func shape(blocks []GatewayContentBlock) []string {
	out := make([]string, 0, len(blocks))
	for _, b := range blocks {
		if b.Type == "image_url" {
			out = append(out, "<image>")
		} else {
			out = append(out, b.Text)
		}
	}
	return out
}

// --- contract with the desktop ---------------------------------------

func readDesktopAttachments(t *testing.T) string {
	t.Helper()
	p := filepath.Join("..", "..", "..", "..", "apps", "desktop", "src", "main", "attachments.ts")
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Skipf("desktop source not present at %s: %v", p, err)
	}
	return string(raw)
}

func tsString(t *testing.T, src, name string) string {
	t.Helper()
	m := regexp.MustCompile(`export const ` + name + ` = "((?:[^"\\]|\\.)*)";`).FindStringSubmatch(src)
	if m == nil {
		t.Fatalf("desktop attachments.ts has no string const %s", name)
	}
	return m[1]
}

func TestImageLabelContract_MatchesDesktop(t *testing.T) {
	src := readDesktopAttachments(t)
	if got := tsString(t, src, "IMAGE_LABEL_TEMPLATE"); got != imageLabelTemplate {
		t.Errorf("IMAGE_LABEL_TEMPLATE: desktop %q, chat-edge %q", got, imageLabelTemplate)
	}
	if got := tsString(t, src, "IMAGE_NOT_SENT_NOTE"); got != imageNotSentNote {
		t.Errorf("IMAGE_NOT_SENT_NOTE: desktop %q, chat-edge %q", got, imageNotSentNote)
	}
	m := regexp.MustCompile(`export const IMAGE_LABEL_MAX_NAME = (\d+);`).FindStringSubmatch(src)
	if m == nil {
		t.Fatal("desktop attachments.ts has no IMAGE_LABEL_MAX_NAME")
	}
	if n, _ := strconv.Atoi(m[1]); n != imageLabelMaxName {
		t.Errorf("IMAGE_LABEL_MAX_NAME: desktop %d, chat-edge %d", n, imageLabelMaxName)
	}
	re := regexp.MustCompile(`export const IMAGE_LABEL_RE = /(.*)/;`).FindStringSubmatch(src)
	if re == nil {
		t.Fatal("desktop attachments.ts has no IMAGE_LABEL_RE")
	}
	if re[1] != imageLabelRe.String() {
		t.Errorf("IMAGE_LABEL_RE: desktop /%s/, chat-edge /%s/", re[1], imageLabelRe.String())
	}
	// The same sample renders the same on both sides (the desktop test pins it).
	if got := imageLabel(2, 3, "design-ref.png"); got != "Image 2 of 3: design-ref.png" {
		t.Errorf("sample label = %q", got)
	}
}
