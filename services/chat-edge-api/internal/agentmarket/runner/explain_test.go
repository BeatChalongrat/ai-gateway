package runner

import (
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// The AM3 stranger's question, word for word, about an invoice register whose
// summary showed net payable 0.00 while 102,970.91 was to be paid.
const strangerQuestion = "ในไฟล์ แผ่นสรุป ยอดชำระสุทธิเป็น 0 บาท ทำไมคะ ต้องจ่ายจริง 102,970.91 ไม่ใช่เหรอคะ"

const invoiceComputed = `{"citations": [
 {"id": "i5", "label": "ยอดชำระสุทธิตามเอกสาร (บาท)", "value": "0.00",
  "formula": "ผลรวมยอดชำระสุทธิเฉพาะที่เอกสารพิมพ์ไว้ เอกสารที่ไม่ได้พิมพ์ยอดนี้ไม่ถูกนับ (ยอดที่ต้องจ่ายจริงดูที่ “ยอดที่ต้องชำระ”)",
  "sources": [{"file": "ใบกำกับภาษี_IV6909-0015.pdf", "value": null, "note": "เอกสารไม่ได้พิมพ์ค่านี้ จึงไม่ได้นับ"},
              {"file": "eTax_ใบเสร็จ.xml", "value": null, "note": "เอกสารไม่ได้พิมพ์ค่านี้ จึงไม่ได้นับ"}],
  "left_out": [], "aliases": ["ยอดชำระสุทธิ", "ชำระสุทธิ"]},
 {"id": "i6", "label": "ยอดที่ต้องชำระ (บาท)", "value": "102970.91",
  "formula": "ของแต่ละฉบับ: ยอดชำระสุทธิที่เอกสารพิมพ์ไว้ ถ้าไม่ได้พิมพ์ ใช้ยอดรวมหักภาษีหัก ณ ที่จ่าย แล้วรวมทุกฉบับที่บันทึกเข้าทะเบียน",
  "sources": [{"file": "ใบกำกับภาษี_IV6909-0015.pdf", "value": "12850.70", "note": "ยอดรวมหักภาษีหัก ณ ที่จ่าย (เอกสารไม่ได้พิมพ์ยอดชำระสุทธิ)"},
              {"file": "eTax_ใบเสร็จ.xml", "value": "90120.21", "note": "ยอดรวมหักภาษีหัก ณ ที่จ่าย (เอกสารไม่ได้พิมพ์ยอดชำระสุทธิ)"}],
  "left_out": [{"file": "รูปใบเสร็จ.jpg", "reason": "ภาพไม่ชัด"}], "aliases": ["ยอดที่ต้องชำระ", "ต้องจ่าย"]}
], "excluded": [{"code": "unreadable_page", "reason": "ภาพไม่ชัด", "name": "รูปใบเสร็จ.jpg"}]}`

func TestAWhyAboutAFigureIsAnsweredFromItsRecordNeverAModel(t *testing.T) {
	ans, theirs := explainAnswer(strangerQuestion, []byte(invoiceComputed))
	// AM6: what the record says is sent as it is — it reads as written for a person
	if !am.ReadsForPerson(ans, theirs...) {
		t.Fatalf("the answer reads as written for a person: %q", am.ShownToPerson(ans, theirs...))
	}
	for _, want := range []string{"ยอดชำระสุทธิตามเอกสาร (บาท) = 0.00 บาท", "เอกสารไม่ได้พิมพ์ค่านี้", "ยอดที่ต้องชำระ (บาท) = 102,970.91 บาท",
		"12,850.70", "90,120.21", "รูปใบเสร็จ.jpg", "ไม่ได้มาจากโมเดล"} {
		if !strings.Contains(ans, want) {
			t.Fatalf("the answer is built from the record (%q missing):\n%s", want, ans)
		}
	}
	if strings.Contains(ans, "ค่าเริ่มต้นของระบบ") {
		t.Fatal("the model's AM3 guess must never come back")
	}
}

func TestAWhyNoRecordSupportsGetsIDontKnow(t *testing.T) {
	if ans, _ := explainAnswer("ทำไมกำไรขั้นต้นลดลงจากเดือนก่อน", []byte(invoiceComputed)); ans != dontKnow || !am.ReadsForPerson(ans) {
		t.Fatalf("no record of that figure: %q", ans)
	}
	// an older package with no records at all
	if ans, _ := explainAnswer("ทำไมยอดเป็นศูนย์", []byte(`{"quotable": {}}`)); ans != dontKnow {
		t.Fatalf("older package: %q", ans)
	}
	// a question that is not a why and names no figure goes on to a model (under the guards)
	if ans, _ := explainAnswer("สรุปให้หน่อย", []byte(invoiceComputed)); ans != "" {
		t.Fatalf("not a figure question: %q", ans)
	}
}

func TestAReportLineIsPreferredOverTheAccountItIsMadeOf(t *testing.T) {
	fin := `{"citations": [
	 {"id": "c1", "label": "4100-01 รายได้จากการขาย งวดนี้", "value": "12480300.00", "formula": "ผลรวม เดบิต − เครดิต ของบัญชีนี้ในงวด",
	  "sources": [{"file": "GL_2569-09.xlsx", "sheet": "GL", "cells": "F12:G900", "rows": 212}]},
	 {"id": "c9", "label": "รายได้จากการขายและบริการ", "value": "12480300.00", "formula": "ผลรวมบัญชีหมวดรายได้", "derived_from": ["c1"]},
	 {"id": "c10", "label": "รายได้อื่น", "value": "3820.55", "formula": "ผลรวมบัญชีหมวดรายได้อื่น", "derived_from": []}
	], "excluded": [{"code": "period_mixed", "reason": "ลงวันที่นอกงวด (12 รายการ)"}]}`
	ans, theirs := explainAnswer("ทำไมรายได้เดือนนี้ถึงเป็นแบบนี้", []byte(fin))
	if !am.ReadsForPerson(ans, theirs...) {
		t.Fatalf("the answer reads as written for a person: %q", am.ShownToPerson(ans, theirs...))
	}
	if !strings.HasPrefix(ans, "**รายได้จากการขายและบริการ = 12,480,300.00 บาท**") || !strings.Contains(ans, "4100-01") ||
		!strings.Contains(ans, "ลงวันที่นอกงวด") {
		t.Fatalf("the report's line, its parts and what was left out:\n%s", ans)
	}
}

func TestAModelAnswerThatGivesACauseIsRefused(t *testing.T) {
	for _, s := range []string{"ยอดเป็น 0 เพราะค่าเริ่มต้นของระบบ", "เนื่องจากไม่มีข้อมูล", "due to the default"} {
		if !givesACause(s) {
			t.Fatalf("%q gives a cause", s)
		}
	}
	if givesACause("รายได้ 12,480,300 บาท") {
		t.Fatal("a value is not a cause")
	}
}
