package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── An agent that does what it is asked (AM11b) ──────────────────────────
//
// The words a person types with a round's files — or after it, about its
// documents — are read by a model into the directives the agent's package
// declares (am.AskedPrompt, am.ReadAsked). The reading is a step of the run:
// asked once, kept with which model answered, on the run's page with every
// other step. What is read is ids; the package's renderer applies them and
// its gate reads the finished file back (the render step), and the thread is
// told what was done as asked and what could not be, with the reason.
//
// The model is one a person's typed words may be sent to (textModels, AM3):
// the agent's own text models where it has a model write its report, and
// only a model inside the organisation for an agent that has none — the same
// rule as a typed question, and what the agent's data sheet says.

// renderAsked is what the render step says of the directives it was given:
// those it applied, and those this round's data could not take.
type renderAsked struct {
	Applied []string `json:"applied"`
	Refused []string `json:"refused"`
}

// specHash names the package's list of directives in a step's key: another
// list is another question.
func specHash(spec am.InstructionSpec) string {
	b, _ := json.Marshal(spec)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:8])
}

// asked reads text into the package's directives. Nothing is asked of a
// model — and nothing is a step — when the package declares none, when there
// are no words, or when the words are only thanks.
//
// request: text is the round's own message of an agent that starts from a request — the words are also
// what it is asked to find, and what is in neither of the package's lists is that, not a refusal.
func (rc *runCtx) asked(ctx context.Context, text string, request bool) (am.Asked, error) {
	text = strings.TrimSpace(text)
	m := rc.pkg.Manifest
	if !m.TakesInstructions() || text == "" || am.SocialOnly(text) || rc.run.Kind == am.KindSelfTest {
		return am.Asked{}, nil
	}
	spec := *m.Instructions
	models := rc.textModels()
	key := am.StepKey(rc.run.ID, "instructions", text, specHash(spec), strings.Join(models, "|"))
	if request {
		key = am.StepKey(rc.run.ID, "instructions", text, specHash(spec), strings.Join(models, "|"), "request")
	}
	raw, err := rc.step(ctx, "instructions", "ai", key, map[string]any{"text": text, "models": models}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			system, user := am.AskedPrompt(spec, text, request)
			// (the model is shown the person's words and the package's list — nothing of the round's data)
			ans, c, refused, away := rc.askJSONWith(ctx, models, system, user, false)
			cost, tries := c.CostUSD, 1
			if ans == nil && !refused {
				// The in-house model sometimes returns nothing for a question it answers the next time
				// (its thinking takes the whole answer). A person is not told to type their words again
				// for that: the same question is put once more, and only a second silence is "unread".
				// (Not when the gateway's guardrail refused it: the same question would be refused again.)
				again, c2, _, away2 := rc.askJSONWith(ctx, models, system, user, false)
				cost, tries, away = cost+c2.CostUSD, 2, away2
				if again != nil {
					ans, c = again, c2
				}
			}
			if ans == nil && away {
				// AM15: nobody could be reached to read the words. That is not "unread" — until now the
				// documents were then made the usual way, delivered and charged, with a line saying the
				// words could not be read. The step fails as a step and the run waits for the model.
				return nil, cost * rc.r.CreditsPerUSD, "", "", ErrModelUnreachable
			}
			out := am.Asked{Text: text, Apply: []string{}, Unread: true}
			if ans != nil {
				out = am.ReadAsked(spec, text, ans)
			}
			if request {
				out = out.OfARequest()
			}
			out.Model = c.Model
			if tries > 1 {
				out.Tries = tries
			}
			return out, cost * rc.r.CreditsPerUSD, "", c.Model, nil
		})
	if err != nil {
		return am.Asked{}, err
	}
	var out am.Asked
	return out, json.Unmarshal(raw, &out)
}

// sayAsked tells the thread what became of what was asked: what the
// documents were made with, and each request that was not done with its
// reason. done is the render step's own account (what it applied, what the
// round's data could not take); a directive it does not account for was not
// applied and is said so.
func (rc *runCtx) sayAsked(ctx context.Context, run am.Run, asked am.Asked, done renderAsked) {
	m := rc.pkg.Manifest
	if !m.TakesInstructions() || asked.Nothing() {
		return
	}
	spec := *m.Instructions
	var lines []am.PersonText
	if asked.Unread {
		lines = append(lines, am.AskedUnread())
	}
	var applied []string
	// (what the renderer could not take is said of what these words asked — a directive in force from
	// an earlier message was spoken of then)
	var refused []string
	for _, id := range done.Refused {
		if contains(asked.Apply, id) {
			refused = append(refused, id)
		}
	}
	for _, id := range asked.Apply {
		switch {
		case contains(done.Applied, id):
			applied = append(applied, id)
		case !contains(refused, id):
			refused = append(refused, id)
		}
	}
	if said := am.AskedDone(spec, applied); !said.EmptyIn(am.Thai) {
		lines = append(lines, said)
	}
	lines = append(lines, am.AskedOneAtATime(spec, asked.Instead)...)
	lines = append(lines, am.AskedNotDone(spec, refused, asked.Cannot)...)
	for _, line := range lines {
		_ = rc.r.Store.PostText(ctx, run, line)
	}
}
