package service

// FIX74 — ask_user_question on the web path. The CONTRACT here is a port of
// apps/desktop/src/main/ask-user.ts and must not drift from it: same tool
// name and description, same limits, same refusals, same three result
// texts. ask_user_contract_test.go reads the TypeScript file and fails if
// any of them differ.
//
// What differs is only WHERE the turn waits: the desktop keeps a promise
// open in the host process; the web path parks the turn in Postgres
// (message_question.go) and resumes it as a new generation when the
// answer arrives.

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const AskUserTool = "ask_user_question"

// The limits — the reference design documents only the header length and
// the two counts; the three text lengths are ours (see the desktop file).
const (
	AskMaxQuestions   = 4
	AskMinOptions     = 2
	AskMaxOptions     = 4
	AskMaxHeader      = 12
	AskMaxQuestion    = 500
	AskMaxLabel       = 80
	AskMaxDescription = 200
	AskMaxFreeText    = 2000
)

// pendingQuestionTTL — how long a parked web turn may wait for its answer
// (Part A §1). Lazy: nothing fires at the deadline; an answer after it is
// refused and the next read of the conversation settles the row.
const pendingQuestionTTL = 24 * time.Hour

const askUserDescription = "Ask the user one to four multiple-choice questions and wait for the answer. The turn pauses until they reply; " +
	"the reply comes back as this tool's result. Use it ONLY for a matter of the user's preference or intent that you " +
	"cannot determine yourself — scope, audience, format, destination — and never for anything you could look up, " +
	"read from the conversation, or reasonably assume. Do not add an 'Other' option: the app adds one that accepts free text."

// The three result texts, identical to the desktop's.
const (
	askSkippedResult = "The user chose not to answer. Proceed on your own judgment, say what you assumed, and do not ask the same question again this turn."
	askRespondedPrefix = "The user responded: "
	askNoAnswer        = "(no answer)"
	// A later tool_use block in the same round as the question: the turn
	// parks at the question, so the later calls never run (the batch
	// contract's shape, with the reason).
	askNotExecutedAfterQuestion = "Not executed: an earlier question in this turn is waiting for the user."
)

var otherLabelRe = regexp.MustCompile(`(?i)^\s*(other|others|something else|none of (the|these) above|อื่น\s*ๆ|อื่นๆ|อื่น|ไม่ใช่ข้อไหนเลย)\s*[.:…]?\s*$`)

type AskOption struct {
	Label       string `json:"label"`
	Description string `json:"description"`
}
type AskQuestion struct {
	Question    string      `json:"question"`
	Header      string      `json:"header"`
	MultiSelect bool        `json:"multiSelect"`
	Options     []AskOption `json:"options"`
}

func askUserToolDef() GatewayTool {
	return GatewayTool{
		Type: "function",
		Function: GatewayToolFunction{
			Name:        AskUserTool,
			Description: askUserDescription,
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"questions": map[string]any{
						"type":        "array",
						"minItems":    1,
						"maxItems":    AskMaxQuestions,
						"description": "1–4 questions. Ask as few as possible; one is usually right.",
						"items": map[string]any{
							"type": "object",
							"properties": map[string]any{
								"question": map[string]any{"type": "string", "maxLength": AskMaxQuestion, "description": "The full question, in the user's language."},
								"header":   map[string]any{"type": "string", "maxLength": AskMaxHeader, "description": "A short chip label for the question (max 12 characters), e.g. 'Format'."},
								"options": map[string]any{
									"type":        "array",
									"minItems":    AskMinOptions,
									"maxItems":    AskMaxOptions,
									"description": "2–4 distinct choices. Never include 'Other' or 'Something else'.",
									"items": map[string]any{
										"type": "object",
										"properties": map[string]any{
											"label":       map[string]any{"type": "string", "maxLength": AskMaxLabel, "description": "The choice as the user will read it; returned verbatim when picked."},
											"description": map[string]any{"type": "string", "maxLength": AskMaxDescription, "description": "Optional one-line explanation of the choice."},
										},
										"required": []string{"label"},
									},
								},
								"multiSelect": map[string]any{"type": "boolean", "description": "true when the user may pick more than one option. Default false."},
							},
							"required": []string{"question", "header", "options"},
						},
					},
				},
				"required": []string{"questions"},
			},
		},
	}
}

// validateAskArgs is the desktop validator, rule for rule. A failure is a
// tool ERROR back to the model — the model can re-ask correctly and the
// user never sees a broken card.
func validateAskArgs(rawArgs string) ([]AskQuestion, error) {
	var args struct {
		Questions []struct {
			Question    string `json:"question"`
			Header      string `json:"header"`
			MultiSelect *bool  `json:"multiSelect"`
			Options     []struct {
				Label       string `json:"label"`
				Description string `json:"description"`
			} `json:"options"`
		} `json:"questions"`
		RawQuestions json.RawMessage `json:"-"`
	}
	var probe map[string]json.RawMessage
	if err := json.Unmarshal([]byte(rawArgs), &probe); err != nil {
		return nil, fmt.Errorf("arguments are not a JSON object")
	}
	if raw, ok := probe["questions"]; !ok || len(raw) == 0 || raw[0] != '[' {
		return nil, fmt.Errorf("'questions' must be an array")
	}
	if err := json.Unmarshal([]byte(rawArgs), &args); err != nil {
		return nil, fmt.Errorf("'questions' must be an array of question objects")
	}
	if len(args.Questions) < 1 {
		return nil, fmt.Errorf("'questions' must hold at least one question")
	}
	if len(args.Questions) > AskMaxQuestions {
		return nil, fmt.Errorf("at most %d questions per call (got %d)", AskMaxQuestions, len(args.Questions))
	}
	out := make([]AskQuestion, 0, len(args.Questions))
	headers := map[string]bool{}
	for i, q := range args.Questions {
		where := fmt.Sprintf("questions[%d]", i)
		question := strings.TrimSpace(q.Question)
		if question == "" {
			return nil, fmt.Errorf("%s.question is required", where)
		}
		if utf8.RuneCountInString(question) > AskMaxQuestion {
			return nil, fmt.Errorf("%s.question is longer than %d characters — put the explanation in your message, keep the question short", where, AskMaxQuestion)
		}
		header := strings.TrimSpace(q.Header)
		if header == "" {
			return nil, fmt.Errorf("%s.header is required (a label of at most %d characters)", where, AskMaxHeader)
		}
		if utf8.RuneCountInString(header) > AskMaxHeader {
			return nil, fmt.Errorf("%s.header is longer than %d characters", where, AskMaxHeader)
		}
		hk := strings.ToLower(header)
		if headers[hk] {
			return nil, fmt.Errorf("%s.header '%s' repeats an earlier question's header", where, header)
		}
		headers[hk] = true
		if len(q.Options) < AskMinOptions || len(q.Options) > AskMaxOptions {
			return nil, fmt.Errorf("%s.options must hold %d–%d choices (got %d)", where, AskMinOptions, AskMaxOptions, len(q.Options))
		}
		labels := map[string]bool{}
		opts := make([]AskOption, 0, len(q.Options))
		for j, o := range q.Options {
			ow := fmt.Sprintf("%s.options[%d]", where, j)
			label := strings.TrimSpace(o.Label)
			if label == "" {
				return nil, fmt.Errorf("%s.label is required", ow)
			}
			if utf8.RuneCountInString(label) > AskMaxLabel {
				return nil, fmt.Errorf("%s.label is longer than %d characters", ow, AskMaxLabel)
			}
			if otherLabelRe.MatchString(label) {
				return nil, fmt.Errorf("%s.label '%s' — do not offer an 'Other' choice; the app adds one that accepts the user's own text", ow, label)
			}
			lk := strings.ToLower(label)
			if labels[lk] {
				return nil, fmt.Errorf("%s.label '%s' repeats an earlier option", ow, label)
			}
			labels[lk] = true
			desc := strings.TrimSpace(o.Description)
			if utf8.RuneCountInString(desc) > AskMaxDescription {
				return nil, fmt.Errorf("%s.description is longer than %d characters", ow, AskMaxDescription)
			}
			opts = append(opts, AskOption{Label: label, Description: desc})
		}
		out = append(out, AskQuestion{Question: question, Header: header, MultiSelect: q.MultiSelect != nil && *q.MultiSelect, Options: opts})
	}
	return out, nil
}

// QuestionAnswer is what the page posts (the desktop's question.respond
// shape): outcome answered|skipped, answers keyed by question text, and an
// optional free reply typed instead of answering.
type QuestionAnswer struct {
	Outcome  string              `json:"outcome"`
	Answers  map[string][]string `json:"answers"`
	Response *string             `json:"response"`
}

// validateQuestionAnswer mirrors bridge-protocol.ts validateQuestionRespond.
func validateQuestionAnswer(a *QuestionAnswer) error {
	if a == nil {
		return fmt.Errorf("payload-not-object")
	}
	if a.Outcome != "answered" && a.Outcome != "skipped" {
		return fmt.Errorf("outcome-invalid")
	}
	if len(a.Answers) > AskMaxQuestions {
		return fmt.Errorf("answers-too-many")
	}
	for q, vals := range a.Answers {
		if q == "" || utf8.RuneCountInString(q) > AskMaxQuestion {
			return fmt.Errorf("answer-key-invalid")
		}
		if len(vals) > AskMaxOptions {
			return fmt.Errorf("answer-not-array")
		}
		for _, v := range vals {
			if utf8.RuneCountInString(v) > AskMaxFreeText {
				return fmt.Errorf("answer-too-long")
			}
		}
	}
	if a.Response != nil && utf8.RuneCountInString(*a.Response) > AskMaxFreeText {
		return fmt.Errorf("response-too-long")
	}
	return nil
}

// formatAnswerForModel is the desktop's formatAnswerForModel: the tool
// result the model reads. Stable wording — the prompt and the tests refer
// to it.
func formatAnswerForModel(questions []AskQuestion, a QuestionAnswer) string {
	if a.Outcome == "skipped" {
		return askSkippedResult
	}
	if a.Response != nil {
		if r := strings.TrimSpace(*a.Response); r != "" {
			buf, _ := json.Marshal(r)
			return askRespondedPrefix + string(buf)
		}
	}
	lines := make([]string, 0, len(questions))
	for _, q := range questions {
		picked := make([]string, 0, 4)
		for _, s := range a.Answers[q.Question] {
			if t := strings.TrimSpace(s); t != "" {
				picked = append(picked, t)
			}
		}
		qb, _ := json.Marshal(q.Question)
		if len(picked) == 0 {
			lines = append(lines, string(qb)+": "+askNoAnswer)
		} else {
			lines = append(lines, string(qb)+": "+strings.Join(picked, ", "))
		}
	}
	return strings.Join(lines, "\n")
}
