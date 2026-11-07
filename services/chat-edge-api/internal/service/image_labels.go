package service

import (
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// FIX76 — image labels.
//
// Every image the request builder ships is announced by a short text block
// RIGHT BEFORE it: "Image 2 of 3: design-ref.png". Without it the model
// resolves "รูป 2" from position alone and can never resolve a filename
// at all. The wording is ONE fixed English form on both surfaces — the
// model reads it, not the user; English tokenises cheapest and the rest of
// the prompt's framing is English. The desktop is the reference copy
// (apps/desktop/src/main/attachments.ts); image_labels_contract_test.go
// reads that file and fails on any drift between the two.
//
// A label must never outlive its image: stripImageBlocks (a model that
// cannot see) drops labels with the blocks, and the history caps decide
// which images exist BEFORE labels are written. Numbers are never
// rewritten after capping — the user's "รูป 3" still means the third image
// they attached.

const (
	imageLabelTemplate = "Image {n} of {total}: {filename}"
	imageLabelMaxName  = 80
)

var imageLabelRe = regexp.MustCompile(`^Image \d+ of \d+: `)

// VisionImage is one image the builder can ship: its data URL plus what
// the label needs. Index/Total are the image's place among ITS message's
// images in attach order, fixed before any cap so pruning never renumbers.
type VisionImage struct {
	URL      string
	Filename string
	Index    int
	Total    int
}

// visionURLs is the []string view callers that only route or budget on the
// images still use.
func visionURLs(imgs []VisionImage) []string {
	if len(imgs) == 0 {
		return nil
	}
	out := make([]string, len(imgs))
	for i, im := range imgs {
		out[i] = im.URL
	}
	return out
}

var controlChars = regexp.MustCompile("[\\x00-\\x1f\\x7f]+")

// imageLabelName is the filename as it appears in a label: control
// characters and line breaks become one space, long names are cut at
// imageLabelMaxName (rune count, so a Thai name is cut on a character).
func imageLabelName(filename string) string {
	clean := strings.TrimSpace(controlChars.ReplaceAllString(filename, " "))
	if clean == "" {
		return "image"
	}
	if utf8.RuneCountInString(clean) > imageLabelMaxName {
		r := []rune(clean)
		return string(r[:imageLabelMaxName-1]) + "…"
	}
	return clean
}

func imageLabel(n, total int, filename string) string {
	s := strings.Replace(imageLabelTemplate, "{n}", strconv.Itoa(n), 1)
	s = strings.Replace(s, "{total}", strconv.Itoa(total), 1)
	return strings.Replace(s, "{filename}", imageLabelName(filename), 1)
}

func isImageLabel(b GatewayContentBlock) bool {
	return b.Type == "text" && imageLabelRe.MatchString(b.Text)
}

// labelledImageBlocks writes, for each image, its label then the image.
// Index/Total come from the image itself when set (the resolver fixed them
// before capping); a bare list is numbered 1..n.
func labelledImageBlocks(imgs []VisionImage, detail string) []GatewayContentBlock {
	blocks := make([]GatewayContentBlock, 0, 2*len(imgs))
	for i, im := range imgs {
		n, total := im.Index, im.Total
		if n == 0 || total == 0 {
			n, total = i+1, len(imgs)
		}
		blocks = append(blocks,
			GatewayContentBlock{Type: "text", Text: imageLabel(n, total, im.Filename)},
			GatewayContentBlock{Type: "image_url", ImageURL: &GatewayImageURL{URL: im.URL, Detail: detail}},
		)
	}
	return blocks
}
