package agentmarket

import (
	"encoding/json"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ── What of a thread item is sent to a person (AM6) ──────────────────────
//
// AM5's guard looked at four kinds of item and a handful of their fields, and
// took out what it recognised. This is the other way round: every kind of
// item has one declared schema, and a field of the agent's side that the
// schema does not declare is not sent — it stays in the row, and what it was
// made from stays on the run's steps (the run page, a problem report).
//
// A declared field is sent by what it is:
//
//	person   what the person typed or named: theirs, sent as it is
//	text     a sentence for a person — the platform's (PersonText), a
//	         package's, a model's: sent as ShownToPerson shows it (reads.go);
//	         when that is nothing the field says the platform's own sentence
//	         for it, or is left out
//	value    a period, an amount, a currency, a code of the person's data
//	data     the person's own data a package quotes as evidence (a cell of
//	         their file, a figure): sent as it is
//	id, code, time, file, model, version   what the web acts on, by its shape
//	key      an identifier the web sends back as it got it; never shown
//
// The schema is applied when an item leaves the store (scanItem), so a row
// written before it existed reads the same way as one written today.

type class int

const (
	asPerson class = iota
	asText
	asValue
	asData
	asID
	asCode
	asKey
	asTime
	asFile
	asModel
	asVersion
	asNumber // a number or a flag: sent whatever its field is; a string in its place must be a number
	asObject
	asList
	asMap // counts by code (confidence, tiers): every value by one rule
)

var classNames = map[class]string{asPerson: "person", asText: "text", asValue: "value", asData: "data", asID: "id", asCode: "code",
	asKey: "key", asTime: "time", asFile: "file", asModel: "model", asVersion: "version", asNumber: "number", asObject: "object",
	asList: "list", asMap: "map"}

// Where a text comes from.
const (
	fromPlatform = "platform" // built by this package from a code and facts (PersonText)
	fromPackage  = "package"  // written by a package's check or script
	fromModel    = "model"    // written by a model
	fromEither   = "platform or model"
	fromBoth     = "platform or package" // a line the platform builds around a package's own words
)

// field is one declared field of an item.
type field struct {
	class class
	from  string
	// other is the platform's own sentence when nothing of a text can be shown
	// (nothing = the field is left out). It may depend on the item. AM11: it is
	// said in the language the item was written in.
	other func(item map[string]any) PersonText
	// value: the text may also be a bare value (no Thai word needed)
	value bool
	// nth: the platform's own sentence names the place in its list ("ตัวเลือกที่ 2"),
	// so that two options whose labels cannot be shown are still two options
	nth    bool
	fields map[string]field
	each   *field
	// declares: an object whose own data is readable in its sentences
	declares func(obj map[string]any) []string
	// alt (AM6): the same key has another shape in another package's summary —
	// a list where this field is counts by code; a value of that shape is sent by alt
	alt *field
}

func text(from string) field { return field{class: asText, from: from} }

// label is a text that may be a bare value: a name, a heading, a period
// ("GL", "กันยายน 2569") needs no Thai word to be one.
func label(from string) field { return field{class: asText, from: from, value: true} }

func (f field) or(sentence wording) field {
	f.other = func(map[string]any) PersonText { return Text(sentence) }
	return f
}

func (f field) orBy(fn func(map[string]any) PersonText) field {
	f.other = fn
	return f
}

func (f field) orNth(sentence wording) field {
	f.nth = true
	return f.or(sentence)
}

func obj(fields map[string]field) field { return field{class: asObject, fields: fields} }

// orList is the field, or — where a package writes a list in its place — that list.
func (f field) orList(l field) field {
	f.alt = &l
	return f
}
func list(of field) field  { return field{class: asList, each: &of} }
func mapOf(of field) field { return field{class: asMap, each: &of} }

var (
	person  = field{class: asPerson}
	value   = field{class: asValue}
	data    = field{class: asData}
	id      = field{class: asID}
	code    = field{class: asCode}
	key     = field{class: asKey}
	moment  = field{class: asTime}
	file    = field{class: asFile}
	model   = field{class: asModel}
	version = field{class: asVersion}
	number  = field{class: asNumber}
	said    = field{class: asText, from: fromPackage, value: true}
)

var fileList = list(obj(map[string]field{"id": id, "name": file, "size": number}))

// evidence is what a question shows of the person's own file. The tables,
// cells and names are the person's data; a sentence of the package's is a
// text; what only the engine reads (its reason in English, a parser's error,
// a rule's name) is not declared, and is not sent.
var evidence = obj(map[string]field{
	"file": file, "document": file, "sheet": data, "sheet_name": data, "column": data, "claim": data, "account": data, "name": data,
	"row": number, "page": number, "header_row": number, "count": number, "be_texts": number, "account_overlap": number,
	"rows": data, "columns": data, "sample_rows": data, "sample_columns": data, "headers": data, "values": data, "sample": data,
	"samples": data, "counts": data, "raw_text": data, "months": data, "currencies": data, "accounts": data, "proposal": data,
	"candidates": data, "groups": data, "entries": data, "docs": data, "bbox": data,
	// AM12: the table a "pick" is made from — the candidates found for one item of a request, every value a
	// quote from a stored page or a code the screen has the words for, and the sources that were not used
	"comparison": data,
	"debit":      value, "credit": value, "diff": value, "debit_total": value, "limit": value, "amount": value,
	"detected": said, "weekday": said, "holiday": said, "expected": said, "actual": said, "other": said,
	"match": obj(map[string]field{"name": data, "document_number": value, "match": said}),
	"fact":  obj(map[string]field{"detail": text(fromPackage)}),
})

// evidenceData is what a question declares as the person's data: what its
// sentences may name without quotation marks.
func evidenceData(q map[string]any) []string {
	ev, _ := q["evidence"].(map[string]any)
	var out []string
	for k, v := range ev {
		if f, ok := evidence.fields[k]; ok && (f.class == asData || f.class == asFile) {
			stringsOf(v, &out)
		}
	}
	return out
}

func stringsOf(v any, out *[]string) {
	switch x := v.(type) {
	case string:
		*out = append(*out, x)
	case []any:
		for _, e := range x {
			stringsOf(e, out)
		}
	case map[string]any:
		for k, e := range x {
			*out = append(*out, k)
			stringsOf(e, out)
		}
	}
}

// namesOf collects the files an object names (its own "name", and its lists').
func namesOf(lists ...string) func(map[string]any) []string {
	return func(o map[string]any) []string {
		var out []string
		if s, ok := o["name"].(string); ok {
			out = append(out, s)
		}
		for _, l := range lists {
			cur := any(o)
			for _, part := range strings.Split(l, ".") {
				m, _ := cur.(map[string]any)
				cur = m[part]
			}
			arr, _ := cur.([]any)
			for _, e := range arr {
				if m, ok := e.(map[string]any); ok {
					if s, ok := m["name"].(string); ok {
						out = append(out, s)
					}
				}
			}
		}
		return out
	}
}

var question = field{class: asObject, declares: evidenceData, fields: map[string]field{
	"key": key, "code": code, "severity": code, "multiSelect": number, "rememberable": number, "leaves_out": number,
	"header":   label(fromPackage).or("คำถาม"),
	"question": text(fromPackage).or("agent มีคำถามที่ยังแสดงเป็นข้อความไม่ได้ — ดูหลักฐานด้านล่างแล้วเลือกคำตอบ หรือกด “แจ้งปัญหา”"),
	"options": list(obj(map[string]field{"label": label(fromPackage).orNth("ตัวเลือกที่"), "description": text(fromPackage),
		"value": key})),
	"default": key, "default_label": label(fromPackage), "recommended_label": label(fromPackage),
	"evidence": evidence,
	"request": list(obj(map[string]field{"role": code, "label": label(fromPackage).or("ไฟล์ที่ agent ต้องใช้"), "why": text(fromPackage),
		"required": number, "accept": list(code)})),
	"input": obj(map[string]field{"kind": code, "hint": text(fromPackage)}),
}}

var qualityChecks = obj(map[string]field{"passed": number,
	"checks": list(obj(map[string]field{"name": code, "ok": number, "label": label(fromPackage).or("การตรวจของระบบ"),
		"detail": label(fromPackage), "values": number})),
	// AM8: a filled customer form's slots that do not hold their value, by the form's own names (the
	// person's data) and why (the package's words) — what the download card lists
	"unfilled": list(obj(map[string]field{"label": person, "why": label(fromPackage)})),
	"review":   obj(map[string]field{"review_id": id, "reviewer": person, "at_label": label(fromPlatform), "note": person})})

var gateOnCard = obj(map[string]field{"passed": number, "code": code, "difference": value,
	"what":    text(fromPackage).orBy(gateOther),
	"next":    text(fromPackage),
	"reasons": list(text(fromPackage))})

// summary is what a package says of its round on the files card. Its keys are
// the packages' own; a new one is sent once it is declared here.
var summary = field{class: asObject, fields: map[string]field{
	"period": value, "period_label": label(fromPackage), "kind": code, "policy": code,
	"revenue": value, "total_expenses": value, "net_profit": value, "balance_diff": value, "difference": value,
	"adjusted_bank": value, "adjusted_book": value, "bank_balance": value, "book_balance": value,
	"claimed_thb": value, "total_thb": value, "vat_thb": value,
	"rows_used": number, "exceptions": number, "has_prior": number, "has_budget": number, "tied": number, "matched": number,
	"by_rule": number, "by_person": number, "items": number, "suspect": number, "journal": number, "run_exceptions": number,
	"review": number, "claims": number, "pass": number, "exception": number, "question": number, "documents": number,
	"included": number, "excluded": number,
	// AM12: a round on the web (the procurement agent's summarize)
	"offers": number, "dropped": number, "sources": number, "chosen": number, "gaps": number, "open_points": number,
	"cap_hit":    number,
	"confidence": mapOf(number),
	// the bank reconciliation's counts by tier; the invoice ↔ PO agent's tiers, each with its own words (AM6)
	"tiers": mapOf(number).orList(list(obj(map[string]field{"tier": code, "label": label(fromPackage), "groups": number,
		"by_rule": number, "by_person": number, "waiting": number}))),
	// AM6: the invoice ↔ PO agent (po_matching's summarize)
	"invoices": number, "lines": number, "passed": number, "blocked": number, "person": number, "three_way": number,
	"two_way": number, "unlinked": number, "over_billed": number, "waiting": number, "receipt_offers": number,
	"value_passed": value, "value_blocked": value, "value_person": value, "received_not_billed": value,
	"billed_not_received": value,
	"practice":            label(fromPackage),
	"by_reason": list(obj(map[string]field{"code": code, "label": label(fromPackage), "outcome": code, "invoices": number,
		"lines": number, "value": value})),
	"by_depth": list(obj(map[string]field{"depth": code, "label": label(fromPackage), "lines": number, "amount": value})),
	"gate":     gateOnCard,
	"models":   list(obj(map[string]field{"model": model, "self_hosted": number, "documents": number})),
	"excluded_list": list(field{class: asObject, declares: namesOf(), fields: map[string]field{"code": code, "name": file,
		"reason": text(fromPackage)}}),
	"not_included": list(field{class: asObject, declares: namesOf(), fields: map[string]field{"doc_key": key, "name": file,
		"reason": text(fromPackage).or("ยังไม่ได้รวมในรอบนี้"), "detail": text(fromPackage), "how": text(fromPackage),
		"can_restore": number}}),
}}

// what a failed round says when nothing of its own text can be shown
func failureWhat(item map[string]any) PersonText {
	reason := ""
	if f, ok := item["failure"].(map[string]any); ok {
		reason, _ = f["reason"].(string)
	}
	return FailureFor(reason, FailureFacts{Kind: KindRun}).What
}

func completionCode(item map[string]any) string {
	c, _ := item["completion"].(map[string]any)
	s, _ := c["code"].(string)
	return s
}

func agentTextOther(item map[string]any) PersonText {
	switch tone, _ := item["tone"].(string); tone {
	case "error":
		return failureWhat(item)
	case "open":
		return CompletionTitle(completionCode(item))
	}
	return NoText
}

// what a round that finished open says when nothing of the package's own
// sentence can be shown
func completionOther(item map[string]any) PersonText { return openWhat(completionCode(item)) }

func gateOther(map[string]any) PersonText { return openWhat("") }

// itemSchema: every kind of thread item, and what of it is sent.
var itemSchema = map[string]field{
	"greeting": obj(map[string]field{
		"text": text(fromPackage), "role": label(fromPackage), "tagline": text(fromPackage), "cannot": text(fromPackage),
		"icon": code, "slug": code, "version": version,
		"needs": list(obj(map[string]field{"role": code, "label": label(fromPackage).or("ไฟล์ที่ agent ต้องใช้"), "note": text(fromPackage),
			"required": number})),
		"produces": list(obj(map[string]field{"format": code, "label": label(fromPackage)})),
		"status":   obj(map[string]field{"self_test_run_id": id}),
	}),
	// AM10: the round's own form, as its item follows it (the slot labels a
	// delivered form lacks are the customer's own, shown as written)
	"form": obj(map[string]field{"run_id": id, "file_id": id, "name": file, "status": code, "open": number,
		"unfilled": list(person), "artifact_id": id, "why": text(fromPackage), "text": text(fromPackage),
		// AM11: the reason each named field has no value; why a form was refused, as a code; the counts
		"unfilled_why": list(text(fromEither)), "reason": code, "fields": number, "mapped": number}),
	// what the person wrote and sent is theirs
	"user_text":  {class: asPerson},
	"user_files": {class: asPerson},
	"agent_text": obj(map[string]field{
		"text":   text(fromEither).orBy(agentTextOther),
		"run_id": id, "tone": code, "review_id": id,
		// ("credits" and "charged" are on items written before AM11b; they pass, and no client shows them)
		"failure": obj(map[string]field{"reason": code, "credits": number, "can_retry": number,
			"what":          text(fromPlatform).orBy(failureWhat),
			"next":          text(fromPlatform),
			"files":         text(fromPlatform),
			"charged":       text(fromPlatform),
			"failed_checks": list(text(fromPackage))}),
		"completion": obj(map[string]field{"code": code, "credits": number,
			"title": text(fromPlatform).orBy(agentTextOther),
			"what":  text(fromPackage).orBy(completionOther),
			"next":  text(fromPackage), "files": text(fromPlatform), "charged": text(fromPlatform)}),
		// AM6: an answer drawn from the person's knowledge base: its name and
		// its files are the person's own; where in a file is the platform's
		"knowledge": obj(map[string]field{"base": person,
			"sources": list(obj(map[string]field{"title": person, "locator": label(fromPlatform)}))}),
	}),
	"notice": obj(map[string]field{
		"text": text(fromPlatform), "link": code, "tone": code, "action": code, "reason": code, "run_id": id, "retry_of": id,
		"share_id": id, "review_id": id, "problem_id": id, "restored": key, "round_start": number, "access": number,
		"contact": obj(map[string]field{"name": person, "email": person}),
	}),
	"card": field{class: asObject, declares: namesOf("files"), fields: map[string]field{
		"card_id": id, "run_id": id, "supersedes": id, "kind": code, "moment": code, "status": code, "answered_via": code,
		"blocking": number, "attempts": number,
		"intro": text(fromPlatform), "note": text(fromPlatform),
		"questions": list(question),
		"answer":    person, // the person's own answer
		"files":     fileList,
	}},
	"progress": obj(map[string]field{
		"run_id": id, "kind": code, "status": code, "outcome": code, "reason": code, "started_at": moment, "finished_at": moment,
		"elapsed_ms": number, "awaiting_ms": number, "sent_out_unmasked": number,
		"steps": list(obj(map[string]field{"key": key, "status": code, "label": label(fromPlatform).or("ขั้นตอนของ agent"),
			"detail": label(fromBoth)})),
	}),
	"outputs": field{class: asObject, declares: namesOf("artifacts", "summary.not_included", "summary.excluded_list"), fields: map[string]field{
		"run_id": id, "period_label": label(fromPackage), "credits": number, "rerender": number, "draft": number,
		"more_formats": list(code), "pdf_of": code,
		"artifacts": list(field{class: asObject, declares: namesOf(), fields: map[string]field{"id": id, "name": file, "format": code,
			"size": number, "version": number, "quality": qualityChecks}}),
		"summary": summary,
		"model": obj(map[string]field{"model": model, "chosen_model": model, "vision_model": model, "choice": model, "uses": code,
			"self_hosted": number, "vision_self_hosted": number, "not_chosen": number, "written_by_rules": number,
			"masking": code, "sent_out_unmasked": number,
			"web": list(obj(map[string]field{"model": model, "self_hosted": number}))}),
		"review": obj(map[string]field{"id": id, "reviewer": person, "at_label": label(fromPlatform), "note": person,
			"skipped": list(label(fromPlatform))}),
		"gate": gateOnCard,
	}},
	"intent": obj(map[string]field{
		"status": code, "choice": code, "handled_as": code, "item_id": id, "run_id": id, "fresh": number,
		"text":       person, // what the person typed
		"files":      fileList,
		"last_round": obj(map[string]field{"run_id": id, "period_label": label(fromPlatform)}),
		"note":       text(fromPlatform),
	}),
	// AM6 section C (email.go): a draft the server built from the round; the
	// recipients and who approved are the person's own
	"email_draft": obj(map[string]field{
		"id": id, "run_id": id, "subject": text(fromPlatform), "body": text(fromPlatform),
		"attachments": list(obj(map[string]field{"artifact_id": id, "name": file, "size": number, "format": code})),
		"can_send":    number, "cannot_send_reason": text(fromPlatform), "body_sha256": key, "created_at": moment,
		"sent": list(obj(map[string]field{"id": id, "draft_id": id, "to": person, "cc": person, "status": code, "at": moment,
			"approved_by": obj(map[string]field{"name": person, "email": person})})),
	}),
}

var (
	idShape   = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	codeShape = regexp.MustCompile(`^[.A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$`)
)

var fileShape = regexp.MustCompile(`\.[A-Za-z0-9]{1,8}$`)

// fileName: a file's name — not a path, not a sentence.
func fileName(s string) bool {
	return len([]rune(s)) <= 255 && !strings.ContainsAny(s, "\n\r\t/\\") && fileShape.MatchString(s)
}

// sending is where a value is sent from: the item, what it declares as the
// person's data, and the value's place in its list (from 1; 0 = in no list).
type sending struct {
	item map[string]any
	r    *Reader
	n    int
	l    Lang // AM11: the language the item was written in
}

// send is what of a value is sent: the value, and whether there is one.
func (f field) send(v any, at sending) (any, bool) {
	r := at.r
	switch x := v.(type) {
	case nil, bool, float64, json.Number:
		if f.class == asObject || f.class == asList || f.class == asMap {
			return nil, x == nil
		}
		return v, true
	case string:
		return f.sendString(x, at)
	case []any:
		switch f.class {
		case asPerson, asData, asKey:
			return v, true
		case asMap, asObject:
			if f.alt != nil {
				return f.alt.send(v, at)
			}
		case asList:
			out := make([]any, 0, len(x))
			for i, e := range x {
				if s, ok := f.each.send(e, sending{at.item, r, i + 1, at.l}); ok {
					out = append(out, s)
				}
			}
			return out, true
		}
	case map[string]any:
		switch f.class {
		case asPerson, asData, asKey:
			return v, true
		case asMap:
			out := make(map[string]any, len(x))
			for k, e := range x {
				if s, ok := f.each.send(e, at); ok && (codeShape.MatchString(k) || r.Value(k)) {
					out[k] = s
				}
			}
			return out, true
		case asObject:
			if f.declares != nil {
				r = r.with(f.declares(x))
			}
			out := make(map[string]any, len(x))
			for k, e := range x {
				sub, declared := f.fields[k]
				if !declared {
					// a number or a flag nobody declared says nothing; anything else is not sent
					switch e.(type) {
					case bool, float64, json.Number:
						out[k] = e
					}
					continue
				}
				if s, ok := sub.send(e, sending{at.item, r, at.n, at.l}); ok {
					out[k] = s
				}
			}
			return out, true
		}
	}
	return nil, false
}

func (f field) sendString(s string, at sending) (any, bool) {
	r, ok := at.r, false
	switch f.class {
	case asPerson, asData:
		ok = true
	case asKey:
		ok = len(s) <= 1000
	case asText:
		if f.value && r.Value(s) {
			return s, true
		}
		shown := r.Shown(s)
		if shown == "" && f.other != nil && strings.TrimSpace(s) != "" {
			if shown = f.other(at.item).In(at.l); f.nth && at.n > 0 {
				shown += " " + strconv.Itoa(at.n)
			}
		}
		if shown == "" && strings.TrimSpace(s) != "" {
			return nil, false
		}
		return shown, true
	case asValue:
		ok = r.Value(s)
	case asNumber:
		ok = r.Value(s) && !strings.ContainsFunc(s, func(c rune) bool { return c >= 'A' })
	case asID:
		ok = idShape.MatchString(s)
	case asCode:
		ok = s == "" || codeShape.MatchString(s)
	case asTime:
		_, err := time.Parse(time.RFC3339Nano, s)
		ok = err == nil
	case asFile:
		ok = fileName(s)
	case asModel:
		ok = s == "" || modelShape.MatchString(s)
	case asVersion:
		ok = s == "" || versionShape.MatchString(s)
	}
	if !ok {
		return nil, false
	}
	return s, true
}

// with is the reader with more declared.
func (r *Reader) with(declared []string) *Reader {
	if len(declared) == 0 {
		return r
	}
	n := NewReader()
	n.thaiOnly = r.thaiOnly
	for k := range r.words {
		n.words[k] = true
	}
	for k := range r.files {
		n.files[k] = true
	}
	n.Declare(declared...)
	return n
}

// ForClient is a thread item's payload as it is sent to a person. A kind
// nobody declared sends nothing of its own.
//
// AM11: an item says which language it was written in ("lang", stamped by
// the thread's writers; none = Thai, which is every item from before). Its
// texts are read by that language's rule — a Thai item's exactly as before —
// and what the platform says in place of a text that cannot be shown is said
// in that language too.
func ForClient(kind string, payload json.RawMessage) json.RawMessage {
	f, ok := itemSchema[kind]
	if !ok {
		return json.RawMessage(`{}`)
	}
	if f.class == asPerson || len(payload) == 0 {
		return payload
	}
	var p map[string]any
	dec := json.NewDecoder(strings.NewReader(string(payload)))
	dec.UseNumber()
	if err := dec.Decode(&p); err != nil || p == nil {
		return json.RawMessage(`{}`)
	}
	l := Thai
	if s, _ := p["lang"].(string); s != "" {
		l = ParseLang(s)
	}
	out, _ := f.send(p, sending{item: p, r: readerFor(l), l: l})
	if m, ok := out.(map[string]any); ok && l == English {
		m["lang"] = string(English)
	}
	b, err := json.Marshal(out)
	if err != nil {
		return json.RawMessage(`{}`)
	}
	return b
}

// stampLang marks a marshalled item as written in l (nothing for Thai: an
// item without the mark is Thai).
func stampLang(l Lang, payload []byte) []byte {
	if l != English || len(payload) < 2 || payload[0] != '{' {
		return payload
	}
	if string(payload) == "{}" {
		return []byte(`{"lang":"en"}`)
	}
	out := make([]byte, 0, len(payload)+13)
	out = append(out, `{"lang":"en",`...)
	return append(out, payload[1:]...)
}

// ShownQuestions are a card's questions as the person was sent them: what
// they clicked is a label they were shown, and an answer is read against
// those (a label that could not be shown was sent as the platform's own).
//
// AM11: l is the language the card was written in — its run's. The card's item is read by that
// language's rule when it is sent, so the labels a person saw are the ones that rule shows: read by
// another, the label clicked on an English card would match none of its options.
func ShownQuestions(l Lang, questions []Question) []Question {
	card := map[string]any{"questions": questions}
	if l == English {
		card["lang"] = string(English)
	}
	raw, err := json.Marshal(card)
	if err != nil {
		return questions
	}
	var sent struct {
		Questions []Question `json:"questions"`
	}
	if json.Unmarshal(ForClient("card", raw), &sent) != nil || len(sent.Questions) != len(questions) {
		return questions
	}
	out := make([]Question, len(questions))
	copy(out, questions)
	for i := range out {
		if len(sent.Questions[i].Options) != len(out[i].Options) {
			continue
		}
		opts := make([]Option, len(out[i].Options))
		copy(opts, out[i].Options)
		for j := range opts {
			opts[j].Label = sent.Questions[i].Options[j].Label
		}
		out[i].Options, out[i].Header = opts, sent.Questions[i].Header
	}
	return out
}

// SchemaLines is the declared schema, one line per kind (the documentation
// and the tests read it).
func SchemaLines() []string {
	var out []string
	kinds := make([]string, 0, len(itemSchema))
	for k := range itemSchema {
		kinds = append(kinds, k)
	}
	sort.Strings(kinds)
	for _, k := range kinds {
		f := itemSchema[k]
		if f.class == asPerson {
			out = append(out, k+": the person's own — sent as it is")
			continue
		}
		var parts []string
		describe("", f, &parts)
		out = append(out, k+": "+strings.Join(parts, ", "))
	}
	return out
}

func describe(path string, f field, out *[]string) {
	switch f.class {
	case asObject:
		names := make([]string, 0, len(f.fields))
		for n := range f.fields {
			names = append(names, n)
		}
		sort.Strings(names)
		for _, n := range names {
			p := n
			if path != "" {
				p = path + "." + n
			}
			describe(p, f.fields[n], out)
		}
	case asList:
		describe(path+"[]", *f.each, out)
	case asMap:
		describe(path+"{}", *f.each, out)
		if f.alt != nil {
			describe(path, *f.alt, out)
		}
	case asText:
		kind := "text"
		if f.value {
			kind = "label"
		}
		*out = append(*out, path+" ("+kind+": "+f.from+")")
	case asNumber:
		// not listed: a number or a flag says nothing a person reads
	default:
		*out = append(*out, path+" ("+classNames[f.class]+")")
	}
}
