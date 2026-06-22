package service

import "strings"

// Sprint 2A — video-generation intent detection. Mirrors image_intent.go.
//
// Detect an explicit video-GENERATION intent in a chat message. Keyword-
// based and deliberately SPECIFIC (action phrases) to limit false
// positives — bare "make"/"create" appear in non-video contexts. The 🎬
// composer mode is the primary signal; this is a secondary hint so a
// tool-capable model is nudged toward generate_video when the user clearly
// asks for a clip.

var thaiVideoKeywords = []string{
	"สร้างวิดีโอ", "ทำวิดีโอ", "สร้างคลิป", "ทำคลิป", "สร้างวีดีโอ",
	"ทำวีดีโอ", "อัดวิดีโอ", "สร้างหนัง",
}

var enVideoKeywords = []string{
	"generate a video", "generate video", "create a video", "create video",
	"make a video", "make me a video", "make a clip", "generate a clip",
	"create a clip", "animate a", "a short video of", "a video of",
}

// DetectVideoIntent reports whether the message is an explicit request to
// GENERATE a video. Specific phrases only — bare "make"/"create" are
// excluded (they appear in non-video contexts like "make a plan").
func DetectVideoIntent(message string) bool {
	lower := strings.ToLower(message)
	for _, kw := range thaiVideoKeywords {
		if strings.Contains(lower, kw) { // Thai has no case; lower is a no-op
			return true
		}
	}
	for _, kw := range enVideoKeywords {
		if strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}
