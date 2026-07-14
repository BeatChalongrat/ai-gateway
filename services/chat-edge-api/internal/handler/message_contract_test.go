// Contract pin for the send-message request body (Ecosystem Fix Phase 11
// Part C — companion to internal/router/contract_test.go).
//
// KNOWN CLIENT/SERVER FIELD DRIFT (documented, intentionally NOT fixed here):
//
//	Both clients send a "video_mode" flag on POST /api/v1/conversations/{id}/messages:
//	  - web-chat:  apps/web-chat/src/lib/api.ts        openMessageStream()  → body.video_mode = true
//	  - desktop:   SranDesktop/.../ChatEdgeApiClient.cs StreamMessageAsync() → body["video_mode"] = true
//	                (its own comment says: "forward-compat; current server reads image_mode only")
//
//	The server's sendRequest struct honors ONLY: content, stream, file_ids,
//	image_mode. "video_mode" is silently dropped by encoding/json (unknown
//	fields are ignored, not rejected) — the 🎬 toggle currently biases
//	nothing server-side on this endpoint.
//
// These tests make that state VISIBLE:
//   - If the server ever starts honoring video_mode (adds the field), the
//     exact-set assertion fails → update this pin AND tell both client
//     owners the flag went live.
//   - If image_mode is ever dropped, the same assertion fails → the
//     web-chat 🎨 toggle and the desktop imageMode flag would go dead.
//   - If the JSON decoding ever becomes strict (DisallowUnknownFields),
//     the tolerance test fails → every current client turn carrying
//     video_mode would start 400ing.
package handler

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// honoredSendFields returns the set of wire field names sendRequest decodes.
func honoredSendFields() map[string]bool {
	fields := map[string]bool{}
	tt := reflect.TypeOf(sendRequest{})
	for i := 0; i < tt.NumField(); i++ {
		tag := tt.Field(i).Tag.Get("json")
		name := strings.Split(tag, ",")[0]
		if name == "" || name == "-" {
			continue
		}
		fields[name] = true
	}
	return fields
}

func TestSendRequestHonoredFieldSetIsPinned(t *testing.T) {
	got := honoredSendFields()
	want := map[string]bool{
		"content":    true,
		"stream":     true,
		"file_ids":   true,
		"image_mode": true,
	}
	for f := range want {
		if !got[f] {
			t.Errorf("sendRequest no longer honors %q — web-chat and desktop clients still send it (see file header)", f)
		}
	}
	for f := range got {
		if !want[f] {
			if f == "video_mode" {
				t.Errorf("sendRequest now honors \"video_mode\" — the known client/server drift is RESOLVED server-side; update this pin and notify the web-chat + desktop owners that their forward-compat flag went live")
				continue
			}
			t.Errorf("sendRequest honors new field %q — add it to this contract pin (and to the clients if they should send it)", f)
		}
	}
}

// TestSendRequestToleratesVideoMode pins the CURRENT behavior for the drift:
// clients sending video_mode get it silently ignored (no decode error, no
// effect), not rejected. If decoding turns strict this fails loudly.
func TestSendRequestToleratesVideoMode(t *testing.T) {
	body := `{"content":"make me a video","stream":true,"image_mode":false,"video_mode":true}`
	var req sendRequest
	if err := json.NewDecoder(strings.NewReader(body)).Decode(&req); err != nil {
		t.Fatalf("decoding a client body carrying video_mode must not error (both shipped clients send it): %v", err)
	}
	if req.Content != "make me a video" || !req.Stream || req.ImageMode {
		t.Fatalf("honored fields decoded wrong: %+v", req)
	}
	// There is intentionally no req.VideoMode to assert on — that absence
	// IS the drift this file documents.
}
