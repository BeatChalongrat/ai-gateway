package runner

import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"

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
