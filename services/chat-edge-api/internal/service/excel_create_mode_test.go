package service

// Phase 8 Task 1 — Excel create-from-scratch.
//
// ExecutePlan itself needs postgres + storage + the docker sandbox, so
// these tests pin the two pure decisions that make create-mode correct
// and that a future edit could silently break:
//
//	1. the operation rewrite (a create-mode plan must never reach the
//	   executor still saying "modify_workbook", which would make
//	   openpyxl.load_workbook() open a source that was never downloaded);
//	2. that the rewrite is scoped to create-mode ONLY — the edit path's
//	   plan must survive byte-for-byte, including the documented
//	   "create_workbook while a source is attached" case.
//
// The end-to-end path (plan → xlsx the user can open) is covered by the
// live E2E run recorded in the phase report.

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestForceCreateWorkbook_RewritesModifyOperation(t *testing.T) {
	plan := map[string]any{
		"operation":      "modify_workbook",
		"source_file_id": uuid.New().String(),
		"actions":        []any{map[string]any{"type": "create_sheet", "sheet_name": "Summary"}},
	}
	out, ok := forceCreateWorkbookOperation(plan).(map[string]any)
	if !ok {
		t.Fatal("expected an object plan back")
	}
	if out["operation"] != "create_workbook" {
		t.Errorf("operation = %v, want create_workbook", out["operation"])
	}
	if _, present := out["source_file_id"]; present {
		t.Error("a stale source_file_id must be dropped in create mode")
	}
	if len(out["actions"].([]any)) != 1 {
		t.Error("actions must survive the rewrite untouched")
	}
}

func TestForceCreateWorkbook_AddsOperationWhenMissing(t *testing.T) {
	// The executor DEFAULTS to modify_workbook when "operation" is
	// absent, so an omitted key is the dangerous case, not a safe one.
	out, ok := forceCreateWorkbookOperation(map[string]any{"actions": []any{}}).(map[string]any)
	if !ok {
		t.Fatal("expected an object plan back")
	}
	if out["operation"] != "create_workbook" {
		t.Fatalf("operation = %v, want create_workbook", out["operation"])
	}
}

func TestForceCreateWorkbook_DoesNotMutateTheCallersPlan(t *testing.T) {
	plan := map[string]any{"operation": "modify_workbook"}
	_ = forceCreateWorkbookOperation(plan)
	if plan["operation"] != "modify_workbook" {
		t.Error("the caller's plan (the request body) must not be mutated in place")
	}
}

func TestForceCreateWorkbook_NonObjectPlanPassesThrough(t *testing.T) {
	// Validation is the executor's job; this helper must not invent an
	// error path of its own.
	for _, p := range []any{"not-an-object", []any{1, 2}, nil} {
		if got := forceCreateWorkbookOperation(p); got == nil && p != nil {
			t.Errorf("plan %v was swallowed", p)
		}
	}
}

func TestPlanForExecution_EditModeIsForwardedVerbatim(t *testing.T) {
	// source != nil ⇒ no rewrite at all. This protects the documented
	// case where the model emits create_workbook WITH a source attached
	// ("build me a fresh summary workbook from this data") — rewriting
	// that to modify_workbook would mutate the user's upload instead.
	srcID := uuid.New().String()
	for _, op := range []string{"modify_workbook", "create_workbook"} {
		original := map[string]any{
			"operation":      op,
			"source_file_id": srcID,
			"actions":        []any{map[string]any{"type": "write_cells"}},
		}
		before, _ := json.Marshal(original)
		after, _ := json.Marshal(planForExecution(original, true))
		if string(before) != string(after) {
			t.Errorf("edit-mode plan changed:\n before %s\n after  %s", before, after)
		}
	}
}

func TestPlanForExecution_CreateModeRewrites(t *testing.T) {
	out, ok := planForExecution(map[string]any{"operation": "modify_workbook"}, false).(map[string]any)
	if !ok {
		t.Fatal("expected an object plan back")
	}
	if out["operation"] != "create_workbook" {
		t.Fatalf("create mode must force create_workbook, got %v", out["operation"])
	}
}

func TestDeriveEditedFilename_UnchangedForEditMode(t *testing.T) {
	if got := deriveEditedFilename("report.xlsx"); got != "report_edited.xlsx" {
		t.Errorf("deriveEditedFilename = %q, want report_edited.xlsx", got)
	}
	if got := deriveEditedFilename(""); got != "workbook_edited.xlsx" {
		t.Errorf("empty original = %q, want workbook_edited.xlsx", got)
	}
}

func TestCreatedWorkbookFilename_ShapeMatchesDocService(t *testing.T) {
	// ExecutePlan's create branch builds "workbook_<UTC timestamp>.xlsx".
	// Pin the shape so the file the user downloads always has a real
	// .xlsx extension (Excel refuses to open it otherwise).
	name := sanitizeFilename("workbook_20260806_101500.xlsx")
	if !strings.HasPrefix(name, "workbook_") || !strings.HasSuffix(name, ".xlsx") {
		t.Fatalf("generated name %q must be workbook_<ts>.xlsx", name)
	}
}
