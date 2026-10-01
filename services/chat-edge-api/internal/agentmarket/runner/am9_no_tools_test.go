package runner

import (
	"reflect"
	"strings"
	"testing"
)

// AM9 (I), rule 1, for agents: an agent's model has no tool at all — the
// request the runner sends a model carries no tools, so no agent can send
// anything (mail included). This fails the day a field for tools is added.
func TestAM9AnAgentsModelIsOfferedNoTool(t *testing.T) {
	typ := reflect.TypeOf(chatReq{})
	for i := 0; i < typ.NumField(); i++ {
		tag := strings.Split(typ.Field(i).Tag.Get("json"), ",")[0]
		if tag == "tools" || tag == "tool_choice" || tag == "functions" || tag == "function_call" {
			t.Fatalf("the agent's model request can carry tools: field %s", typ.Field(i).Name)
		}
	}
	t.Logf("ASSERT the agent model request (%d fields) has no tools field", typ.NumField())
}
