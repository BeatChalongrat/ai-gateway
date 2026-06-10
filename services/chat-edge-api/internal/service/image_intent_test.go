package service

import (
	"strings"
	"testing"
)

func TestDetectImageIntentThai(t *testing.T) {
	yes := []string{"สร้างรูปแมวส้มให้หน่อย", "ช่วยวาดภาพบ้านริมทะเล", "วาดรูปแมว", "ทำภาพพระอาทิตย์ตก"}
	for _, s := range yes {
		if !DetectImageIntent(s) {
			t.Errorf("expected image intent for %q", s)
		}
	}
	no := []string{"สวัสดีครับ", "อธิบาย machine learning หน่อย", "ขอบคุณครับ", "สรุปรูปแบบข้อมูลนี้"}
	for _, s := range no {
		if DetectImageIntent(s) {
			t.Errorf("did NOT expect image intent for %q", s)
		}
	}
}

func TestDetectImageIntentEnglish(t *testing.T) {
	yes := []string{
		"draw me a cute cat", "generate an image of a sunset",
		"create a picture of a dog", "make an image of a house",
		"GENERATE IMAGE of mountains",
	}
	for _, s := range yes {
		if !DetectImageIntent(s) {
			t.Errorf("expected image intent for %q", s)
		}
	}
	no := []string{
		"draw a conclusion from the data", "describe the painting",
		"create a plan for the project", "summarize this image", "what is in the picture",
	}
	for _, s := range no {
		if DetectImageIntent(s) {
			t.Errorf("did NOT expect image intent for %q", s)
		}
	}
}

// --- Phase D: reject (not override) when the model can't generate images ---

func TestNonToolModel_ImageIntent_Rejects(t *testing.T) {
	// non-tool model + image intent -> REJECT (helpful message, no override)
	if !shouldRejectImageTurn(false /*toolCapable*/, true /*intent*/, false /*mode*/) {
		t.Error("non-tool model + image intent should be rejected")
	}
}

func TestToolModel_ImageIntent_Proceeds(t *testing.T) {
	// tool-capable model + image intent -> proceed (it calls the tool itself)
	if shouldRejectImageTurn(true, true, false) {
		t.Error("tool-capable model should NOT be rejected on image intent")
	}
}

func TestNonToolModel_NoImageIntent_Proceeds(t *testing.T) {
	// non-tool model + no image intent -> normal chat, proceed
	if shouldRejectImageTurn(false, false, false) {
		t.Error("non-image turn should never be rejected")
	}
}

func TestNonToolModel_ImageMode_Rejects(t *testing.T) {
	// 🎨 mode on a non-tool model (safety net — 🎨 is hidden for these) -> reject
	if !shouldRejectImageTurn(false, false, true) {
		t.Error("image mode on a non-tool model should be rejected")
	}
}

func TestToolModel_ImageMode_Proceeds(t *testing.T) {
	if shouldRejectImageTurn(true, false, true) {
		t.Error("tool-capable model in image mode should NOT be rejected")
	}
}

func TestImageUnsupportedMessage(t *testing.T) {
	msg := imageUnsupportedMessage("deepseek/deepseek-r1")
	if !strings.Contains(msg, "deepseek/deepseek-r1") {
		t.Errorf("message should name the model, got %q", msg)
	}
	if !strings.Contains(msg, "🎨") {
		t.Errorf("message should point at 🎨 models, got %q", msg)
	}
}
