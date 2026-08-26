package service

// FIX16 §1.4 — the CLOUD TOOL BOUNDARY, kept honest by tests.
//
// Three guarantees:
//   1. EXHAUSTIVE: every tool the registry can emit is classified in
//      model as allowed-or-withheld. Add a tool def below without placing
//      it in model.CloudAllowedTools / cloudWithheldTools and this fails —
//      the next tool has to make the decision on the record.
//   2. DEFAULT-DENY: an unknown/new tool name is NOT cloud-allowed, so even
//      an unclassified tool is safe by default on an unattended run.
//   3. GUARDED AT RUNTIME: executeToolCalls refuses a call to a withheld
//      tool on a cloud run — the tool never executes and the run is flagged
//      so the wall is visible (§2), instead of a silently worse answer.

import (
	"context"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// liveToolNames mirrors ToolExecutor.ToolDefs: every *ToolDef the registry
// can offer. Kept beside the classification so a new tool def surfaces here
// and trips guarantee (1). (The def funcs are package-level, so this
// enumerates the real names, not a hand-typed copy.)
func liveToolNames() []string {
	var names []string
	for _, d := range MemoryToolDefs() { // save_memory, list_memories
		names = append(names, d.Function.Name)
	}
	for _, d := range []GatewayTool{
		searchPastConversationsToolDef(),
		useSkillToolDef(),
		updateConversationStateToolDef(),
		generateImageToolDef(),
		generateVideoToolDef(),
		webSearchToolDef(),
		webFetchToolDef(),
		runPythonToolDef(),
		updateCanvasArtifactToolDef(),
	} {
		names = append(names, d.Function.Name)
	}
	return names
}

func TestCloudToolBoundary_EveryRegisteredToolIsClassified(t *testing.T) {
	allowed, withheld := model.CloudToolClassification()
	live := liveToolNames()

	// (1) every live tool is in exactly one bucket.
	for _, n := range live {
		_, a := allowed[n]
		_, w := withheld[n]
		if a == w { // both or neither
			t.Errorf("tool %q must be classified in EXACTLY ONE of CloudAllowedTools / cloudWithheldTools (allowed=%v withheld=%v)", n, a, w)
		}
	}
	// No stale classification: every named tool must actually exist.
	inLive := make(map[string]bool, len(live))
	for _, n := range live {
		inLive[n] = true
	}
	for n := range allowed {
		if !inLive[n] {
			t.Errorf("CloudAllowedTools names %q which is not a live tool", n)
		}
	}
	for n := range withheld {
		if !inLive[n] {
			t.Errorf("cloudWithheldTools names %q which is not a live tool", n)
		}
	}
}

func TestCloudToolBoundary_DefaultDeny(t *testing.T) {
	// A tool nobody has classified yet is NOT cloud-allowed — safe by default.
	if model.IsCloudToolAllowed("some_future_write_tool") {
		t.Fatal("an unclassified tool must default to NOT cloud-allowed")
	}
	// The known-dangerous ones are withheld.
	for _, n := range []string{"generate_image", "generate_video", "save_memory", "update_canvas_artifact", "update_conversation_state"} {
		if model.IsCloudToolAllowed(n) {
			t.Errorf("%q must NOT be cloud-allowed", n)
		}
	}
	// The read-only set is allowed.
	for _, n := range []string{"search_past_conversations", "web_search", "web_fetch", "run_python", "list_memories", "use_skill"} {
		if !model.IsCloudToolAllowed(n) {
			t.Errorf("%q must be cloud-allowed", n)
		}
	}
}

func TestExecuteToolCalls_CloudRun_RefusesWithheldTool(t *testing.T) {
	s := &MessageService{toolExec: &ToolExecutor{}}
	var hit bool
	// A cloud run whose allowlist is the real read-only set.
	allow := make(map[string]bool)
	for _, n := range model.CloudAllowedToolNames() {
		allow[n] = true
	}
	ec := ToolExecContext{CloudToolAllow: allow, WithheldHit: &hit}

	// generate_image is withheld; search_past_conversations is allowed
	// (dlpMarkerArgs makes it return via the safe early path, no wired svc).
	_, results := s.executeToolCalls(context.Background(), []GatewayToolCall{
		call("c1", "generate_image", `{"prompt":"a cat"}`),
		call("c2", "search_past_conversations", dlpMarkerArgs),
	}, ec)

	if len(results) != 2 {
		t.Fatalf("expected 2 tool results, got %d", len(results))
	}
	if !hit {
		t.Fatal("WithheldHit must be set when a withheld tool is requested on a cloud run")
	}
	// The withheld call returned the refusal text and did NOT execute the tool.
	got, _ := results[0].Content.(string)
	if !containsSubstr(got, "not available in an unattended scheduled run") {
		t.Fatalf("withheld tool result must explain the wall, got %q", got)
	}
}

func containsSubstr(s, sub string) bool {
	return len(s) >= len(sub) && (func() bool {
		for i := 0; i+len(sub) <= len(s); i++ {
			if s[i:i+len(sub)] == sub {
				return true
			}
		}
		return false
	})()
}
