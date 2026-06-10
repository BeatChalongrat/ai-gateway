package service

import "strings"

// Phase D — smart image routing.
//
// Detect an explicit image-GENERATION intent in a chat message, and decide
// whether to override the turn's model to a tool-capable one. Keyword-based:
// image requests are explicit, so no ML is needed. Kept deliberately
// SPECIFIC (action phrases, not bare "draw"/"create"/"design") — a false
// positive forces a one-turn model override to ImageFallbackModel, which
// costs a worse answer if the user actually wanted their reasoning model.

// ImageFallbackModel is the cheap, fast, tool-capable model a non-tool
// model is swapped to for an image-generation turn. Per-turn only — the
// conversation's model is never changed, so the next turn reverts.
const ImageFallbackModel = "gpt-4o-mini"

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

// decideImageRoute is the pure per-turn routing decision. When the turn
// wants an image (explicit 🎨 mode OR detected intent) and the current
// model can't call tools, it returns the tool-capable fallback. Otherwise
// it keeps the current model. Never mutates conversation state.
func decideImageRoute(
	currentModel string, toolCapable, hasIntent, isImageMode bool,
) (model string, overridden bool) {
	if !hasIntent && !isImageMode {
		return currentModel, false
	}
	if toolCapable {
		return currentModel, false // model can call generate_image itself
	}
	return ImageFallbackModel, true
}
