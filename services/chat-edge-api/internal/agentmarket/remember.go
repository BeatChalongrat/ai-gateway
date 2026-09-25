package agentmarket

import (
	"context"
	"encoding/json"

	"github.com/google/uuid"
)

// RememberAnswers offers every answer a person gave to a rememberable
// question as the install's default for next time, so the same question is
// not asked again. A value the engine defaulted is never remembered — only
// what a person chose. Returns how many were remembered (the caller posts
// the one-line "จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้" notice when > 0).
func (s *Store) RememberAnswers(ctx context.Context, q Querier, run Run, cardID uuid.UUID, questions []Question,
	answers map[string]Answer) (int, error) {
	defs, err := s.ListDefaults(ctx, run.InstallID)
	if err != nil {
		return 0, err
	}
	current := map[string]json.RawMessage{}
	for _, d := range defs {
		current[d.Key] = d.Value
	}
	n := 0
	rid := run.ID
	cid := cardID
	for _, qn := range questions {
		a, ok := answers[qn.Key]
		if !ok || !qn.Rememberable || (a.Via != "card" && a.Via != "text") {
			continue
		}
		var key string
		var value json.RawMessage
		label := qn.Header + ": " + a.Label
		switch qn.Code {
		case "ambiguous_debit_credit":
			var ev struct {
				Column string `json:"column"`
			}
			_ = json.Unmarshal(qn.Evidence, &ev)
			if ev.Column == "" {
				continue
			}
			m := map[string]json.RawMessage{}
			_ = json.Unmarshal(current["dc_convention"], &m)
			m[ev.Column] = a.Value
			key, value = "dc_convention", mustJSON(m)
			label = "การอ่านคอลัมน์ “" + ev.Column + "”: " + a.Label
		case "unmapped_column":
			var ev struct {
				Proposal map[string]string `json:"proposal"`
			}
			_ = json.Unmarshal(qn.Evidence, &ev)
			m := map[string]string{}
			_ = json.Unmarshal(current["mapping"], &m)
			var chosen struct {
				Mapping map[string]string `json:"mapping"`
			}
			var accept string
			if json.Unmarshal(a.Value, &accept) == nil && accept == "accept" {
				for f, h := range ev.Proposal {
					m[f] = h
				}
			} else if json.Unmarshal(a.Value, &chosen) == nil && len(chosen.Mapping) > 0 {
				for f, h := range chosen.Mapping {
					m[f] = h
				}
			} else {
				continue
			}
			key, value = "mapping", mustJSON(m)
			label = "การจับคู่คอลัมน์"
		default:
			key, value = "answers."+qn.Code, a.Value
		}
		if err := s.SetDefault(ctx, q, run.InstallID, Default{Key: key, Value: value, Source: "answer", Label: label,
			Note: "จำจากคำตอบในรอบ " + run.ID.String()[:8], FromRunID: &rid, FromCardID: &cid}); err != nil {
			return n, err
		}
		current[key] = value
		n++
	}
	return n, nil
}

func mustJSON(v any) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}

// DefaultsForEngine shapes install defaults as the package code reads them:
// {mapping, dc_convention, answers{code: value}, currency, formats, …}.
func DefaultsForEngine(defs []Default) (map[string]any, map[string]json.RawMessage) {
	out := map[string]any{}
	remembered := map[string]json.RawMessage{}
	answers := map[string]json.RawMessage{}
	for _, d := range defs {
		if len(d.Key) > 8 && d.Key[:8] == "answers." {
			answers[d.Key[8:]] = d.Value
			remembered[d.Key[8:]] = d.Value
			continue
		}
		var v any
		_ = json.Unmarshal(d.Value, &v)
		out[d.Key] = v
	}
	out["answers"] = answers
	return out, remembered
}
