package runner

import (
	"fmt"
	"unicode"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── What a prompt tells a model about languages (AM11) ───────────────────
//
// A prompt is addressed to a model, and is written in Thai whatever the
// run's language: a package's own instructions are, and a model follows
// either. Two things in a prompt are not about the prompt but about the run,
// and name the run's language: which language the person typed in, and which
// language the answer is to be written in — an answer is shown to the person
// as it comes, so a run in English is answered in English.

// languageName is a language as a prompt names it.
func languageName(l am.Lang) string {
	if l == am.English {
		return "ภาษาอังกฤษ"
	}
	return "ภาษาไทย"
}

// answerIn is the line that tells a model which language to answer in, put
// last, where it is read last. A Thai run's prompt has none and is exactly
// what it was: its instructions are Thai, and so is its answer.
func answerIn(l am.Lang) string {
	if l != am.English {
		return ""
	}
	return " เขียนคำตอบทั้งหมดเป็นภาษาอังกฤษ แม้คำสั่งนี้จะเป็นภาษาไทย (Write the whole answer in English.)"
}

// directionWord is the example a prompt gives of a direction said in words.
func directionWord(l am.Lang) string {
	if l == am.English {
		return "decreased"
	}
	return "ลดลง"
}

// ── AM22 §1.2, §1.6: the one retry's reason, and the length reasons, in the run's language ──────
//
// A model is told its budget and told why its last answer failed in the language it writes in: an
// English run was told both in Thai before (CARD AM-22 §1.6). These are addressed to a model and are
// not person-texts; they are listed as such in am11_language_test.go.

// columns is the one count of a text's length the validator measures with (AM22 §1.4; the engine's
// agent_market/columns.py is the same count): the characters a reader sees — a combining mark (a Thai
// tone mark, a vowel above or below the line) takes no column.
func columns(s string) int {
	n := 0
	for _, r := range s {
		if unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Me, r) {
			continue
		}
		n++
	}
	return n
}

// inLang is a reason sent back to a model in the language of its answer (AM22 §1.6): the Thai as it always was,
// the English for an English run.
func inLang(l am.Lang, thai, english string) string {
	if l == am.English {
		return english
	}
	return thai
}

// retryLine is what is added to the prompt on the one retry: the reason the last answer could not be
// used, and that the answer is to come again as JSON in the same shape.
func retryLine(l am.Lang, reason string) string {
	if l == am.English {
		return "\n\nThe previous answer could not be used because: " + reason + ". Correct it and answer again as JSON in the same shape."
	}
	return "\n\nคำตอบก่อนหน้าใช้ไม่ได้เพราะ: " + reason + " แก้แล้วตอบใหม่เป็น JSON ตามรูปแบบเดิม"
}

// where names the part of the answer a reason is about: the summary, or the n-th point of a list.
func where(l am.Lang, list string, n int) string {
	if n == 0 {
		return list
	}
	if l == am.English {
		return fmt.Sprintf("%s, point %d", list, n)
	}
	return fmt.Sprintf("%s ข้อที่ %d", list, n)
}

// tooLong: a part of the answer is longer than the document takes. It asks for an edit that keeps every
// point — the copy editor's remedy — never for a cut.
func tooLong(l am.Lang, list string, n, got, limit int) string {
	if l == am.English {
		return fmt.Sprintf("%s is %d characters; the document takes at most %d (characters a reader sees). Edit it down to that length without dropping a point",
			where(l, list, n), got, limit)
	}
	return fmt.Sprintf("%s ยาว %d ตัวอักษร เอกสารรับได้ไม่เกิน %d (นับตัวอักษรที่อ่านเห็น) เกลาให้สั้นลงเท่านั้นโดยไม่ตัดประเด็นใดทิ้ง",
		where(l, list, n), got, limit)
}

// tooMany: a heading holds more points than the document takes at all.
func tooMany(l am.Lang, list string, got, limit int) string {
	if l == am.English {
		return fmt.Sprintf("%s has %d points; the document takes at most %d under one heading. Fold the points together so that none is lost", list, got, limit)
	}
	return fmt.Sprintf("%s มี %d ข้อ เอกสารรับได้ไม่เกิน %d ข้อต่อหัวข้อ รวมข้อเข้าด้วยกันโดยไม่ให้ประเด็นใดหายไป", list, got, limit)
}

// notAList: a list was sent as something else (a sentence, a number).
func notAList(l am.Lang, list string) string {
	if l == am.English {
		return fmt.Sprintf("%s must be a JSON list of sentences (an empty list when there is nothing to say)", list)
	}
	return fmt.Sprintf("%s ต้องเป็นรายการ JSON ของประโยค (รายการว่างถ้าไม่มีอะไรจะบอก)", list)
}

// notASentence: a member of a list is not a sentence (a number, an object, an empty string).
func notASentence(l am.Lang, list string, n int) string {
	if l == am.English {
		return fmt.Sprintf("%s is not a sentence: every member of %s must be one sentence of text", where(l, list, n), list)
	}
	return fmt.Sprintf("%s ไม่ใช่ประโยค ทุกข้อใน %s ต้องเป็นข้อความหนึ่งประโยค", where(l, list, n), list)
}
