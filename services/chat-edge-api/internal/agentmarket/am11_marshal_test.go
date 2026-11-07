package agentmarket

import (
	"bytes"
	"encoding/json"
	"sync"
	"sync/atomic"
	"testing"
)

// AM11 — the language of one MarshalIn is that call's only.
//
// The server writes for many people at once: one request's item is written
// in English while another's is written by a plain json.Marshal, which says
// Thai. If the first call's language could be seen by the second, a Thai
// person's thread would hold an English sentence — which the reader of a
// Thai item then withholds, so they would see nothing at all.

const marshalSentence = "จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้"

func TestAPlainMarshalNeverTakesAnotherCallsLanguage(t *testing.T) {
	p := Text(marshalSentence)
	wantThai, _ := json.Marshal(p.In(Thai))
	wantEnglish, _ := json.Marshal(p.In(English))
	if bytes.Equal(wantThai, wantEnglish) {
		t.Fatal("the test needs a sentence that has an English")
	}
	var wg sync.WaitGroup
	var wrongThai, wrongEnglish atomic.Int32
	for g := 0; g < 8; g++ {
		wg.Add(3)
		go func() {
			defer wg.Done()
			for i := 0; i < 4000; i++ {
				if b, _ := MarshalIn(English, p); !bytes.Equal(b, wantEnglish) {
					wrongEnglish.Add(1)
				}
			}
		}()
		go func() {
			defer wg.Done()
			for i := 0; i < 4000; i++ {
				// a writer that was never taught a language
				if b, _ := json.Marshal(p); !bytes.Equal(b, wantThai) {
					wrongThai.Add(1)
				}
			}
		}()
		go func() {
			defer wg.Done()
			for i := 0; i < 4000; i++ {
				if b, _ := MarshalIn(Thai, p); !bytes.Equal(b, wantThai) {
					wrongThai.Add(1)
				}
			}
		}()
	}
	wg.Wait()
	if wrongThai.Load() != 0 || wrongEnglish.Load() != 0 {
		t.Fatalf("written in another call's language: %d Thai writes came out English, %d English writes came out Thai",
			wrongThai.Load(), wrongEnglish.Load())
	}
}

// thaiInside writes its text in Thai whatever it is written inside (a row kept for a Thai reader).
type thaiInside struct{ text PersonText }

func (v thaiInside) MarshalJSON() ([]byte, error) { return MarshalIn(Thai, v.text) }

func TestAMarshalInsideAnotherKeepsItsOwnLanguageAndGivesTheOutersBack(t *testing.T) {
	p := Text(marshalSentence)
	got, err := MarshalIn(English, []any{p, thaiInside{p}, p})
	if err != nil {
		t.Fatal(err)
	}
	en, _ := json.Marshal(p.In(English))
	th, _ := json.Marshal(p.In(Thai))
	want := "[" + string(en) + "," + string(th) + "," + string(en) + "]"
	if string(got) != want {
		t.Fatalf("got %s\nwant %s", got, want)
	}
	// and nothing is left behind for the next writer on this goroutine
	if after, _ := json.Marshal(p); !bytes.Equal(after, th) {
		t.Fatalf("a plain marshal after an English one wrote %s", after)
	}
}
