package service

// FIX112 Part A — two shared texts moved into assets; the web's rendered
// output is byte for byte what it was. (The desktop's copies are pinned
// against the same assets by apps/desktop/test/fix112-prompt-pins.test.ts.)

import (
	"os"
	"strings"
	"testing"
)

func TestFIX112_TheTemporaryChatNoticeIsByteForByteWhatItWas(t *testing.T) {
	const was = "<temporary_chat>\n" +
		"This is a temporary chat. Nothing said here will be remembered: you have no tool that can save anything, and no fact, preference, or instruction stated in this conversation will be available in any later one.\n" +
		"If the user asks you to remember something, tell them plainly that you cannot in a temporary chat, and that they can turn memory back on for it. Never say or imply that you have saved, noted, or will recall anything.\n" +
		"The conversation itself is still saved to the user's chat history as normal — it is the remembering that is off, not the record.\n" +
		"</temporary_chat>"
	if temporaryChatNotice != was {
		t.Fatalf("the temporary-chat notice changed:\n%s", temporaryChatNotice)
	}
	// The asset holds the body alone — three sentences, no envelope.
	raw, err := os.ReadFile("prompt_temporary_chat.txt")
	if err != nil {
		t.Fatal(err)
	}
	body := strings.TrimSpace(strings.ReplaceAll(string(raw), "\r\n", "\n"))
	if strings.Contains(body, "<temporary_chat>") || strings.HasPrefix(body, "This is a temporary chat.") {
		t.Fatal("the asset must carry the body only; the envelope and the lead-in are each surface's own")
	}
	if len(strings.Split(body, "\n")) != 3 {
		t.Fatalf("the asset must hold exactly the three sentences, one per line; got %d lines", len(strings.Split(body, "\n")))
	}
}

func TestFIX112_TheMemoryFrameIsByteForByteWhatItWas(t *testing.T) {
	if memoryStandingLine != "The user set these standing instructions. Follow them in every response, the same as instructions in the current message, unless the current message explicitly overrides them:" {
		t.Fatalf("standing-instructions lead changed: %q", memoryStandingLine)
	}
	if memoryClosingLine != "Do not say 'according to my memory' or mention the memory system." {
		t.Fatalf("closing sentence changed: %q", memoryClosingLine)
	}
	raw, err := os.ReadFile("prompt_memory_frame.txt")
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(strings.ReplaceAll(string(raw), "\r\n", "\n")), "\n")
	if len(lines) != 2 {
		t.Fatalf("the asset holds the two shared sentences and nothing else; got %d lines", len(lines))
	}
}
