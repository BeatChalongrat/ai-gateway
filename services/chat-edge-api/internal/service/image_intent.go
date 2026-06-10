package service

import "strings"

// Phase D — image-generation intent detection + capability gating.
//
// Detect an explicit image-GENERATION intent in a chat message. When the
// turn wants an image but the selected model can't call tools, the send
// path REJECTS with a helpful message (telling the user to pick a 🎨 model)
// rather than silently swapping models — so the user keeps control of which
// model answers. Keyword-based: image requests are explicit, no ML needed.
// Kept deliberately SPECIFIC (action phrases) to limit false positives.

var thaiImageKeywords = []string{
	"สร้างรูป", "วาดรูป", "ทำรูป", "สร้างภาพ", "วาดภาพ",
	"ออกแบบรูป", "วาดให้", "ช่วยวาด", "สร้างรูปภาพ", "ทำภาพ",
}

var enImageKeywords = []string{
	"draw me", "draw a picture", "draw an image", "draw a pic",
	"generate an image", "generate a image", "generate image",
	"generate a picture", "create an image", "create a image",
	"create image", "create a picture", "make an image", "make a image",
	"make a picture", "paint me", "illustrate a", "sketch a",
}

// DetectImageIntent reports whether the message is an explicit request to
// GENERATE an image. Specific phrases only — bare "draw"/"create"/"design"
// are intentionally excluded (they appear in non-image contexts like
// "draw a conclusion" or "create a plan").
func DetectImageIntent(message string) bool {
	lower := strings.ToLower(message)
	for _, kw := range thaiImageKeywords {
		if strings.Contains(lower, kw) { // Thai has no case; lower is a no-op
			return true
		}
	}
	for _, kw := range enImageKeywords {
		if strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}

// shouldRejectImageTurn is the pure per-turn decision. When the turn wants
// an image (explicit 🎨 mode OR detected intent) and the model is NOT
// tool-capable, return true → the caller replies with a helpful "pick a 🎨
// model" message instead of calling the gateway. Otherwise false → the
// normal flow proceeds (a tool-capable model calls generate_image itself).
func shouldRejectImageTurn(toolCapable, hasIntent, isImageMode bool) bool {
	if !hasIntent && !isImageMode {
		return false // not an image turn
	}
	return !toolCapable
}

// imageUnsupportedMessage is the assistant reply shown when an image turn
// lands on a model that can't generate images. Generic + friendly — the
// model id stays in the server log (not the user-facing text).
func imageUnsupportedMessage() string {
	return "โมเดลที่คุณเลือกไม่สามารถสร้างรูปได้ครับ " +
		"กรุณาเลือกโมเดลที่มีไอคอน 🎨 เช่น GPT-4o หรือ Claude"
}
