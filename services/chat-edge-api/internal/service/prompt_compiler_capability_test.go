package service

// Capability-statement consistency tests (Phase-1 quick-win fix).
//
// The old Layer-5 block was a static const that told the model it
// could NOT run code / browse / create images even when run_python /
// web_search / generate_image were attached to the very same request.
// These tests pin the new contract: the CANNOT list never contradicts
// a tool in ActiveTools, and the honest denials come back when the
// tools are absent.

import (
	"strings"
	"testing"
)

func compileCapability(tools []string) string {
	pc := &PromptCompiler{}
	return pc.capabilityStatement(CompileContext{ActiveTools: tools})
}

func TestCapabilityStatement_NoTools_KeepsHonestDenials(t *testing.T) {
	got := compileCapability(nil)

	for _, want := range []string{
		"run code",
		"browse the internet or open URLs",
		"create or edit images",
		"access real-time data",
		"send email or call external services",
		"access the user's device, camera, or microphone",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("no-tools statement should still deny %q; got:\n%s", want, got)
		}
	}
	if !strings.Contains(got, "<capabilities>") || !strings.Contains(got, "</capabilities>") {
		t.Errorf("statement lost its <capabilities> envelope:\n%s", got)
	}
}

func TestCapabilityStatement_ActiveTools_RemoveContradictions(t *testing.T) {
	got := compileCapability([]string{
		"run_python", "web_search", "generate_image", "generate_video",
	})

	cannot := got[strings.Index(got, "You CANNOT"):]
	for _, banned := range []string{
		"run code",
		"browse the internet or open URLs",
		"create or edit images",
		"access real-time data",
	} {
		if strings.Contains(cannot, banned) {
			t.Errorf("CANNOT list still denies %q while its tool is attached:\n%s", banned, got)
		}
	}

	can := got[:strings.Index(got, "You CANNOT")]
	for _, want := range []string{"run_python", "web_search", "generate_image", "generate_video"} {
		if !strings.Contains(can, want) {
			t.Errorf("CAN list should mention the %s tool:\n%s", want, got)
		}
	}

	// Still-true denials survive.
	for _, want := range []string{
		"send email or call external services",
		"access the user's device, camera, or microphone",
	} {
		if !strings.Contains(cannot, want) {
			t.Errorf("always-true denial %q went missing:\n%s", want, got)
		}
	}
}

func TestCapabilityStatement_PartialTools(t *testing.T) {
	// web_search on, run_python off: real-time denial goes away, the
	// run-code denial stays.
	got := compileCapability([]string{"web_search"})
	cannot := got[strings.Index(got, "You CANNOT"):]
	if strings.Contains(cannot, "access real-time data") {
		t.Errorf("real-time denial should drop when web_search is active:\n%s", got)
	}
	if !strings.Contains(cannot, "run code") {
		t.Errorf("run-code denial should remain without run_python:\n%s", got)
	}
}

func TestToolPolicy_CoversAllEightTools(t *testing.T) {
	pc := &PromptCompiler{}
	all := []string{
		"save_memory", "list_memories", "search_past_conversations",
		"update_conversation_state", "web_search", "web_fetch", "run_python",
		"generate_image", "generate_video",
	}
	got := pc.toolPolicy(CompileContext{ActiveTools: all})
	for _, name := range all {
		if !strings.Contains(got, "- "+name+":") {
			t.Errorf("tool_policy missing a paragraph for %s:\n%s", name, got)
		}
	}
}

func TestSafetyBlock_NoBrowseRunCodeEnumeration(t *testing.T) {
	pc := &PromptCompiler{}
	got := pc.safetyRules(CompileContext{})
	if strings.Contains(got, "pretend to browse, run code") {
		t.Errorf("safety block still enumerates browse/run-code denials:\n%s", got)
	}
	if !strings.Contains(got, "capabilities you lack") {
		t.Errorf("safety block lost the generic no-pretending rule:\n%s", got)
	}
}
