package service

import "strings"

// Image-generation intent detection.
//
// Detect an explicit image-GENERATION intent in a chat message. An image
// turn (🎨 mode or detected intent) is dispatched by the send path straight
// to the image endpoint (directImageStream) — the chat model is not
// involved, so there is nothing here about whether it can call tools.
// FIX63 removed the Phase D "reject when the model lacks tools" branch:
// the direct dispatch made it unreachable, and the composer both clients
// share holds an image request at a model that cannot make images before
// anything is sent (FIX62). Keyword-based: image requests are explicit, no
// ML needed. Kept deliberately SPECIFIC (action phrases) to limit false
// positives. The web-chat composer carries the same list verbatim
// (apps/web-chat/src/lib/imageIntent.ts) — keep them in step.

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
