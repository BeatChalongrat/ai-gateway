package service

import "testing"

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

func TestDecideImageRoute(t *testing.T) {
	// tool-capable model + intent -> keep model
	if m, ov := decideImageRoute("gpt-4o", true, true, false); ov || m != "gpt-4o" {
		t.Errorf("tool-capable should not override: got %q overridden=%v", m, ov)
	}
	// non-tool model + intent -> override to fallback
	if m, ov := decideImageRoute("deepseek-r1", false, true, false); !ov || m != ImageFallbackModel {
		t.Errorf("non-tool should override to %s: got %q overridden=%v", ImageFallbackModel, m, ov)
	}
	// no intent + no mode -> keep model (normal chat)
	if m, ov := decideImageRoute("deepseek-r1", false, false, false); ov || m != "deepseek-r1" {
		t.Errorf("no intent should not override: got %q overridden=%v", m, ov)
	}
	// 🎨 image mode on non-tool model -> override
	if m, ov := decideImageRoute("deepseek-r1", false, false, true); !ov || m != ImageFallbackModel {
		t.Errorf("image mode should override: got %q overridden=%v", m, ov)
	}
	// 🎨 image mode on tool-capable model -> keep (it can call the tool)
	if m, ov := decideImageRoute("claude-sonnet-4", true, false, true); ov || m != "claude-sonnet-4" {
		t.Errorf("tool-capable image mode should not override: got %q overridden=%v", m, ov)
	}
}
