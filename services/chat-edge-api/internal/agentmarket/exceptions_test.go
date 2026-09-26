package agentmarket

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"
)

func expenseExceptions() []ExceptionIn {
	choices := json.RawMessage(`[{"label":"อนุมัติเป็นกรณียกเว้น","value":"approve_exception"},{"label":"ขอเอกสารเพิ่ม","value":"ask_employee"}]`)
	return []ExceptionIn{
		{ReasonCode: "over_claim_limit", Reason: "เกินวงเงินต่อครั้ง", Severity: "review", Subject: "C02",
			SourceRef:      json.RawMessage(`{"claim":"C02","row":3,"limit":"1500.00","limit_source":"policy_table"}`),
			ProposedAction: "ให้ผู้อนุมัติตัดสิน", Choices: choices},
		{ReasonCode: "weekend_or_holiday", Reason: "วันหยุด", Severity: "info", Subject: "C07",
			SourceRef: json.RawMessage(`{"claim":"C07"}`), ProposedAction: "บันทึกไว้", Choices: json.RawMessage(`[]`)},
	}
}

func TestRunExceptionsAreListedAndDecidedOnlyByTheirOwnChoices(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	if n, err := e.store.ReplaceRunExceptions(ctx, run.ID, expenseExceptions()); err != nil || n != 1 {
		t.Fatalf("stored: %d %v (one waits on a person, the info row is only noted)", n, err)
	}
	code, body := e.do("GET", "/runs/"+run.ID.String()+"/exceptions", nil)
	rows, _ := body["exceptions"].([]any)
	if code != 200 || len(rows) != 2 {
		t.Fatalf("list: %d %v", code, body)
	}
	open := rows[0].(map[string]any)
	if open["status"] != "open" || open["reason_code"] != "over_claim_limit" || rows[1].(map[string]any)["status"] != "noted" {
		t.Fatalf("open first, info noted: %v", rows)
	}
	ref := open["source_ref"].(map[string]any)
	if ref["subject"] != "C02" || ref["limit_source"] != "policy_table" || ref["row"] != float64(3) {
		t.Fatalf("the evidence is kept: %v", ref)
	}
	id := open["id"].(string)

	// another user sees neither the list nor the decision
	other := uuid.New().String()
	if code, _ := e.do("GET", "/runs/"+run.ID.String()+"/exceptions", nil, "X-Test-User", other); code != 404 {
		t.Fatalf("another user's list: %d", code)
	}
	if code, _ := e.do("POST", "/exceptions/"+id+"/decision", map[string]any{"choice": "approve_exception"}, "X-Test-User", other); code != 404 {
		t.Fatalf("another user's decision: %d", code)
	}
	// there is no hidden "reject": only the exception's own choices
	if code, _ := e.do("POST", "/exceptions/"+id+"/decision", map[string]any{"choice": "reject"}); code != 400 {
		t.Fatalf("a choice the exception does not offer: %d", code)
	}
	// an info row offers nothing to decide
	info := rows[1].(map[string]any)["id"].(string)
	if code, _ := e.do("POST", "/exceptions/"+info+"/decision", map[string]any{"choice": "approve_exception"}); code != 409 {
		t.Fatalf("an info row is not waiting on anyone: %d", code)
	}
	code, dec := e.do("POST", "/exceptions/"+id+"/decision", map[string]any{"choice": "ask_employee", "note": "ขอใบเสร็จตัวจริง"})
	if code != 200 {
		t.Fatalf("decide: %d %v", code, dec)
	}
	got := dec["exception"].(map[string]any)
	d := got["source_ref"].(map[string]any)["decision"].(map[string]any)
	if got["status"] != "resolved" || got["resolved_at"] == nil || d["choice"] != "ask_employee" || d["label"] != "ขอเอกสารเพิ่ม" || d["note"] != "ขอใบเสร็จตัวจริง" {
		t.Fatalf("the decision is recorded: %v", got)
	}
	var by uuid.UUID
	_ = e.store.Pool.QueryRow(ctx, `SELECT resolved_by FROM agent_market.agent_run_exceptions WHERE id = $1`, id).Scan(&by)
	if by != e.user.UserID {
		t.Fatal("who decided is recorded")
	}
	if code, _ := e.do("POST", "/exceptions/"+id+"/decision", map[string]any{"choice": "approve_exception"}); code != 409 {
		t.Fatalf("a decided exception is not decided again: %d", code)
	}

	// the run is stored again (a re-run of the same step): the decision stays, nothing duplicates
	if n, err := e.store.ReplaceRunExceptions(ctx, run.ID, expenseExceptions()); err != nil || n != 0 {
		t.Fatalf("re-stored: %d %v", n, err)
	}
	var total, resolved int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*), count(*) FILTER (WHERE status = 'resolved')
		FROM agent_market.agent_run_exceptions WHERE run_id = $1`, run.ID).Scan(&total, &resolved)
	if total != 2 || resolved != 1 {
		t.Fatalf("after a re-store: %d rows, %d resolved", total, resolved)
	}
}
