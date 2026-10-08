package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM14 — an agent's name and its greeting are read as the screen reads them, not as the screen it was
// installed from did. The owner saw both on an English screen: the greeting in Thai (the first thing
// anyone sees) and a Thai name in the sidebar beside an English header.

const (
	am14ThaiName  = "กระทบยอดธนาคาร"
	am14EnName    = "Bank Reconciliation"
	am14ThaiGreet = "สวัสดีครับ ผมกระทบยอดเงินฝากธนาคารให้"
	am14EnGreet   = "Hello. I reconcile the bank account for you."
	am14ThaiNeed  = "รายการเดินบัญชีจากธนาคาร"
	am14EnNeed    = "Bank statement"
	am14ThaiOut   = "กระดาษทำการ .xlsx"
	am14EnOut     = "Working paper (.xlsx)"
)

// am14Manifest is the test manifest with what a package of today carries: an English name, a short name,
// and the English of every sentence it shows a person.
func am14Manifest(t *testing.T, version, shortName, nameEN string) json.RawMessage {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(testManifest), &m); err != nil {
		t.Fatal(err)
	}
	m["version"], m["short_name"], m["name_en"] = version, shortName, nameEN
	m["greeting"] = map[string]any{"text": am14ThaiGreet}
	m["inputs"] = []any{map[string]any{"role": "primary", "label": am14ThaiNeed, "required": true}}
	m["outputs"] = []any{map[string]any{"format": "xlsx", "label": am14ThaiOut, "default": true}}
	en := map[string]any{shortName: nameEN, am14ThaiGreet: am14EnGreet, am14ThaiNeed: am14EnNeed, am14ThaiOut: am14EnOut}
	if cannot, _ := m["cannot"].(string); cannot != "" {
		en[cannot] = "Read-only." // (every sentence of a real manifest has its English: test_manifest_english.py)
	}
	m["en"] = en
	b, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func (e *testEnv) am14Install(version, shortName, nameEN string, hdr ...string) Install {
	e.t.Helper()
	if _, err := e.store.UpsertDefinition(context.Background(), Definition{Slug: "sran.financial-report", Version: version,
		Name: shortName, Manifest: am14Manifest(e.t, version, shortName, nameEN), Checksum: "pkgsum-" + version, EngineChecksum: "engsum"}); err != nil {
		e.t.Fatal(err)
	}
	e.heartbeat(fullCaps())
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"}, hdr...)
	if code != http.StatusOK {
		e.t.Fatalf("install %d %v", code, body)
	}
	in, err := e.store.GetInstall(context.Background(), e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	if err != nil {
		e.t.Fatal(err)
	}
	return in
}

// am14Screen is what one screen is shown of an install: its name in the three places a list or a page
// takes it from, and its greeting.
type am14Screen struct {
	page, list, rail string
	greeting         map[string]any
}

func (e *testEnv) am14Read(in Install, hdr ...string) am14Screen {
	e.t.Helper()
	var s am14Screen
	_, one := e.do("GET", "/installs/"+in.ID.String(), nil, hdr...)
	s.page, _ = one["install"].(map[string]any)["title"].(string)
	_, all := e.do("GET", "/installs", nil, hdr...)
	for _, x := range all["installs"].([]any) {
		if x.(map[string]any)["id"] == in.ID.String() {
			s.list, _ = x.(map[string]any)["title"].(string)
		}
	}
	_, rail := e.do("GET", "/rail", nil, hdr...)
	for _, x := range rail["installs"].([]any) {
		if x.(map[string]any)["id"] == in.ID.String() {
			s.rail, _ = x.(map[string]any)["title"].(string)
		}
	}
	_, th := e.do("GET", "/installs/"+in.ID.String()+"/thread", nil, hdr...)
	for _, it := range th["items"].([]any) {
		if it.(map[string]any)["kind"] == "greeting" {
			s.greeting, _ = it.(map[string]any)["payload"].(map[string]any)
		}
	}
	return s
}

func am14First(list any, key string) string {
	xs, _ := list.([]any)
	if len(xs) == 0 {
		return ""
	}
	s, _ := xs[0].(map[string]any)[key].(string)
	return s
}

func TestAnAgentInstalledFromAThaiScreenIntroducesItselfInEnglishOnAnEnglishScreen(t *testing.T) {
	e := newEnv(t)
	in := e.am14Install("1.0.0", am14ThaiName, am14EnName) // a Thai screen: the row keeps the Thai name
	if in.Title != am14ThaiName {
		t.Fatalf("the row keeps %q", in.Title)
	}

	th := e.am14Read(in)
	if th.page != am14ThaiName || th.list != am14ThaiName || th.rail != am14ThaiName {
		t.Fatalf("a Thai screen: page %q, list %q, sidebar %q", th.page, th.list, th.rail)
	}
	if th.greeting["text"] != am14ThaiGreet || am14First(th.greeting["needs"], "label") != am14ThaiNeed || th.greeting["lang"] != nil {
		t.Fatalf("a Thai screen's greeting: %v", th.greeting)
	}

	en := e.am14Read(in, "Accept-Language", "en")
	if en.page != am14EnName || en.list != am14EnName || en.rail != am14EnName {
		t.Fatalf("an English screen: page %q, list %q, sidebar %q — want %q in all three", en.page, en.list, en.rail, am14EnName)
	}
	g := en.greeting
	if g["text"] != am14EnGreet || am14First(g["needs"], "label") != am14EnNeed || am14First(g["produces"], "label") != am14EnOut || g["lang"] != "en" {
		t.Fatalf("an English screen's greeting: %v", g)
	}
	if b, _ := json.Marshal(g); strings.ContainsAny(string(b), "กขคงจฉชซญดตถทธนบปผฝพฟภมยรลวศษสหอฮ") {
		t.Fatalf("a Thai letter is left in the greeting an English screen is shown: %s", b)
	}
	// what the item says of the day it was written is still there
	if g["slug"] != "sran.financial-report" || g["version"] != "1.0.0" {
		t.Fatalf("the greeting's own facts: %v", g)
	}
	// and nothing was rewritten: the row and the stored item are as they were
	again, _ := e.store.GetInstall(context.Background(), e.user.TenantUUID, e.user.UserID, in.ID)
	if again.Title != am14ThaiName {
		t.Fatalf("reading in English rewrote the row: %q", again.Title)
	}
	if back := e.am14Read(in); back.rail != am14ThaiName || back.greeting["text"] != am14ThaiGreet {
		t.Fatalf("a Thai screen afterwards: %q / %v", back.rail, back.greeting["text"])
	}
}

func TestAnAgentInstalledFromAnEnglishScreenIsThaiOnAThaiScreen(t *testing.T) {
	e := newEnv(t)
	in := e.am14Install("1.0.0", am14ThaiName, am14EnName, "Accept-Language", "en")
	if in.Title != am14EnName {
		t.Fatalf("installed from an English screen the row keeps %q", in.Title)
	}
	th := e.am14Read(in)
	if th.page != am14ThaiName || th.list != am14ThaiName || th.rail != am14ThaiName {
		t.Fatalf("a Thai screen: page %q, list %q, sidebar %q — want %q", th.page, th.list, th.rail, am14ThaiName)
	}
	if th.greeting["text"] != am14ThaiGreet || th.greeting["lang"] != nil {
		t.Fatalf("a Thai screen's greeting: %v", th.greeting)
	}
	if en := e.am14Read(in, "Accept-Language", "en"); en.rail != am14EnName || en.greeting["text"] != am14EnGreet {
		t.Fatalf("an English screen: %q / %v", en.rail, en.greeting["text"])
	}
}

func TestAnAgentThatWasRenamedIsListedUnderItsNameOfToday(t *testing.T) {
	// the owner's own install: made at 1.0.0 as "ปิดงบการเงิน", an agent since renamed
	e := newEnv(t)
	in := e.am14Install("1.0.0", "ปิดงบการเงิน", "")
	if _, err := e.store.UpsertDefinition(context.Background(), Definition{Slug: "sran.financial-report", Version: "1.8.0",
		Name: "รายงานผลประกอบการ", Manifest: am14Manifest(t, "1.8.0", "รายงานผลประกอบการ", "Financial Report"), Checksum: "pkgsum-180", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.DefinitionBySlug(context.Background(), "sran.financial-report"); err != nil {
		t.Fatal(err)
	}
	if th := e.am14Read(in); th.rail != "รายงานผลประกอบการ" || th.page != "รายงานผลประกอบการ" {
		t.Fatalf("a Thai screen lists it as %q / %q", th.rail, th.page)
	}
	if en := e.am14Read(in, "Accept-Language", "en"); en.rail != "Financial Report" || en.list != "Financial Report" {
		t.Fatalf("an English screen lists it as %q / %q", en.rail, en.list)
	}
	if again, _ := e.store.GetInstall(context.Background(), e.user.TenantUUID, e.user.UserID, in.ID); again.Title != "ปิดงบการเงิน" {
		t.Fatalf("the row itself is not rewritten: %q", again.Title)
	}
}

func TestANameNobodyKnowsIsShownAsItWasKept(t *testing.T) {
	if got := AgentNameIn(English, "sran.no-such-agent", "ชื่อที่เก็บไว้"); got != "ชื่อที่เก็บไว้" {
		t.Fatalf("an agent whose package is not known here: %q", got)
	}
	learnAgentName(json.RawMessage(`{"id":"sran.am14-thai-only","version":"1.0.0","name":"ชื่อเต็ม","short_name":"ชื่อสั้น"}`))
	if got := AgentNameIn(English, "sran.am14-thai-only", "ชื่อที่เก็บไว้"); got != "ชื่อที่เก็บไว้" {
		t.Fatalf("a package with no English name: %q", got)
	}
	if got := AgentNameIn(Thai, "sran.am14-thai-only", "Kept"); got != "ชื่อสั้น" {
		t.Fatalf("its Thai name: %q", got)
	}
	// an older version read after a newer one does not take the name back
	learnAgentName(json.RawMessage(`{"id":"sran.am14-renamed","version":"1.8.0","short_name":"ชื่อใหม่","name_en":"New name"}`))
	learnAgentName(json.RawMessage(`{"id":"sran.am14-renamed","version":"1.0.0","short_name":"ชื่อเก่า","name_en":"Old name"}`))
	if th, en := AgentNameIn(Thai, "sran.am14-renamed", ""), AgentNameIn(English, "sran.am14-renamed", ""); th != "ชื่อใหม่" || en != "New name" {
		t.Fatalf("the newest version names the agent: %q / %q", th, en)
	}
	// and in a payload built where the screen's language is not at hand
	b, err := MarshalIn(English, map[string]any{"title": agentTitle{"sran.am14-renamed", "ชื่อเก่า"}})
	if err != nil || string(b) != `{"title":"New name"}` {
		t.Fatalf("%s %v", b, err)
	}
}

// What a package's engine says while it runs is kept in its manifest by key ("strings", and the English of
// one key in "en_by_key"). A screen is sent a manifest's words for a person — never that list.
func TestAScreenIsNotSentTheListOfEverythingAnEngineCanSay(t *testing.T) {
	raw := json.RawMessage(`{"id":"sran.am14-engine","version":"1.0.0","name":"ชื่อ","name_en":"Name","cannot":"อ่านอย่างเดียว",
		"strings":{"card.header":"ผู้ขาย","field.seller":"ผู้ขาย","q":"อ่าน{what}ไม่ได้"},
		"en":{"ชื่อ":"Name","อ่านอย่างเดียว":"Read-only.","ผู้ขาย":"seller","อ่าน{what}ไม่ได้":"{what} could not be read"},
		"en_by_key":{"card.header":"Zzyzx seller"}}`)
	for _, l := range []Lang{Thai, English} {
		var sent map[string]any
		if err := json.Unmarshal(ManifestIn(l, raw), &sent); err != nil {
			t.Fatal(err)
		}
		for _, k := range []string{"strings", "en", "en_by_key"} {
			if _, there := sent[k]; there {
				t.Errorf("%s: a screen is sent %q", l, k)
			}
		}
		if sent["id"] != "sran.am14-engine" || sent["cannot"] == nil {
			t.Errorf("%s: the manifest's own words are sent: %v", l, sent)
		}
	}
	// the server keeps them: it reads a reason's words from the manifest it stores …
	if w := reasonWords(json.RawMessage(`{"strings":{"drop.not_read":"อ่านไม่ได้"},"en":{"อ่านไม่ได้":"could not be read"}}`), English); w["not_read"] != "could not be read" {
		t.Errorf("reasonWords: %v", w)
	}
	// … and learns the English kept by key as the package's own words, like the rest of its English
	if ReadsIn(English, "Zzyzx seller") {
		t.Fatal("the word is known before the manifest is learnt: the test proves nothing")
	}
	AddPackageEnglish(raw)
	if !ReadsIn(English, "Zzyzx seller") {
		t.Error("a heading kept by its key is not among the package's English words")
	}
}
