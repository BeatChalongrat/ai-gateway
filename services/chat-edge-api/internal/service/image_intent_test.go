package service

import (
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
