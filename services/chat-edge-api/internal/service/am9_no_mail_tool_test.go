package service

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// AM9 (I), rule 1: the mail connector is in no model's toolset. A model
// drafts; the app sends exactly what a person approved (agentmarket/email.go,
// personmail.go). This fails the day any tool a chat model can be offered —
// any GatewayToolFunction declared anywhere in chat-edge's code, offered or
// not yet — is named for sending mail, or the cloud allowlist names one.
var mailTool = regexp.MustCompile(`(?i)mail|send_message|smtp|gmail|outbox`)

func toolNamesDeclared(t *testing.T, root string) map[string]string {
	t.Helper()
	names := map[string]string{}
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		f, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(f, func(n ast.Node) bool {
			lit, ok := n.(*ast.CompositeLit)
			if !ok {
				return true
			}
			typ := ""
			switch x := lit.Type.(type) {
			case *ast.Ident:
				typ = x.Name
			case *ast.SelectorExpr:
				typ = x.Sel.Name
			}
			if typ != "GatewayToolFunction" {
				return true
			}
			for _, el := range lit.Elts {
				kv, ok := el.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				if k, ok := kv.Key.(*ast.Ident); ok && k.Name == "Name" {
					if v, ok := kv.Value.(*ast.BasicLit); ok {
						s, _ := strconv.Unquote(v.Value)
						names[s] = path
					}
				}
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return names
}

func TestAM9NoChatModelToolSendsMail(t *testing.T) {
	names := toolNamesDeclared(t, "..")
	if len(names) < 5 {
		t.Fatalf("the scan found the chat's tools: %v", names)
	}
	for name, where := range names {
		if mailTool.MatchString(name) {
			t.Fatalf("a model tool that sends mail: %q in %s", name, where)
		}
	}
	for _, d := range []GatewayTool{webFetchToolDef(), webSearchToolDef(), runPythonToolDef(), useSkillToolDef(),
		updateCanvasArtifactToolDef(), generateImageToolDef(), generateVideoToolDef(), searchPastConversationsToolDef()} {
		if mailTool.MatchString(d.Function.Name) {
			t.Fatalf("offered: %q", d.Function.Name)
		}
	}
	for _, d := range MemoryToolDefs() {
		if mailTool.MatchString(d.Function.Name) {
			t.Fatalf("offered: %q", d.Function.Name)
		}
	}
	t.Logf("ASSERT none of the %d tools a chat model can be offered sends mail", len(names))
}
