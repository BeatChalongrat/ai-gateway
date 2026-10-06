package runner

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── A form for this round only (AM10 E) ──────────────────────────────────
//
// inspectRoundForm reads a form dropped into a round (the same inspection
// and model proposal as the install's form, template.go) and writes what it
// found on the round: ready when every slot that needs a decision has a
// proposal the mapping check accepts — then the fill follows at once if the
// round has its figures, or in its render step — and needs_mapping otherwise,
// which opens the mapping screen on the form's item. AM11: a form the model
// read and found a value for less than half of the blanks it expects filled
// is refused before any work, with those blanks named (decideForm). The
// install's saved form is never touched.

func (r *Runner) inspectRoundForm(ctx context.Context, rc *runCtx, job am.Run, f am.File, runID uuid.UUID) error {
	form, err := r.Store.RoundForm(ctx, r.Store.Pool, runID)
	if err != nil || form == nil || form.FileID != f.ID {
		return r.fail(ctx, job, "template", errors.New("the round has no such form"))
	}
	parent, err := r.Store.RunByID(ctx, runID)
	if err != nil {
		return r.fail(ctx, job, "template", err)
	}
	key := am.StepKey(job.ID, "form", f.SHA256, rc.pkg.Checksum)
	out, err := rc.step(ctx, "form:"+f.Name, "ingest", key, map[string]any{"file": f.Name}, false,
		func(ctx context.Context) (any, float64, string, string, error) {
			path, cleanup, err := r.Files.OpenLocalCopy(ctx, f.StorageKey)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			input := "template" + filepath.Ext(f.Name)
			res, err := rc.exec(ctx, map[string]any{"op": "inspect_template", "package": rc.pkg.PyPackage,
				"file": map[string]any{"name": f.Name, "input": input}, "saved": nil,
				"spec": json.RawMessage(rc.pkg.Manifest.Templates)}, []Input{{Name: input, LocalPath: path}})
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			o, credits, model := rc.proposeMapping(ctx, mustJSON(res.Result))
			return o, rc.sandboxCredits(res.Elapsed) + credits, "deliverables/template_out.py", model, nil
		})
	if err != nil {
		form.Status, form.Why = am.FormFailed, ""
		_ = r.Store.SyncFormItem(ctx, r.Store.Pool, parent, form)
		_ = r.Store.SetRoundForm(ctx, r.Store.Pool, runID, form)
		return r.fail(ctx, job, "template", err)
	}
	var res struct {
		Slots      []am.TemplateSlot `json:"slots"`
		Proposal   map[string]string `json:"proposal"`
		Blocking   []json.RawMessage `json:"blocking"`
		ProposedBy string            `json:"proposed_by"`
		ReadBy     string            `json:"read_by"`
	}
	_ = json.Unmarshal(mustJSON(out), &res)
	form.Slots, form.Proposal, form.ProposedBy = res.Slots, res.Proposal, res.ProposedBy
	var spec am.TemplateSpec
	_ = json.Unmarshal(rc.pkg.Manifest.Templates, &spec)
	form.Status, form.Why = decideForm(res.Slots, res.Proposal, res.ReadBy, res.Blocking, spec)
	if form.Status == am.FormReady {
		form.Mapping = res.Proposal
	}
	if err := r.Store.SyncFormItem(ctx, r.Store.Pool, parent, form); err != nil {
		return r.fail(ctx, job, "template", err)
	}
	if err := r.Store.SetRoundForm(ctx, r.Store.Pool, runID, form); err != nil {
		return r.fail(ctx, job, "template", err)
	}
	if form.Status == am.FormReady && parent.Status == am.RunSucceeded {
		if _, err := r.formFillJob(ctx, parent); err != nil {
			return r.fail(ctx, job, "template", err)
		}
	}
	return r.finish(rc, am.RunSucceeded, "")
}

// decideForm says what becomes of a form that was just read (AM11), in this order:
//
//	failed         the filler cannot use it at all (a chart it would leave with old numbers, a marker it
//	               cannot reach) — the filler's reason;
//	refused        nothing in the file takes a value; or a model read the form's labels and the agent has
//	               a value for less than half of the blanks the form expects filled. Nothing is made; the
//	               blanks are named and a person may map them themselves;
//	ready          every slot that needs a decision has a proposal the mapping check accepts;
//	needs_mapping  everything else: a person decides on the mapping screen, and the save runs the same
//	               refusal check (am.CheckFormCoverage).
//
// Without a model that read the form (none the organisation lets see documents) nothing is known about
// what the agent could fill, so the form is never refused for it here: the person maps it.
func decideForm(slots []am.TemplateSlot, proposal map[string]string, readBy string, blocking []json.RawMessage,
	spec am.TemplateSpec) (status, why string) {
	if len(blocking) > 0 {
		return am.FormFailed, blockingWhy(blocking)
	}
	refusal, _, _ := am.FormRefusal(slots, proposal)
	switch {
	case refusal == am.RefusedNoFields:
		return am.FormRefused, refusal
	case formObvious(slots, proposal, spec):
		return am.FormReady, ""
	case refusal == am.RefusedTooFew && readBy != "":
		return am.FormRefused, refusal
	}
	return am.FormNeedsMapping, ""
}

// formObvious: every slot that needs a decision has a proposal the mapping check accepts — nothing for
// a person to decide. A cell beside a title or a note of an unmarked form (am.NeedsDecision) is offered
// on the mapping screen and needs none.
func formObvious(slots []am.TemplateSlot, proposal map[string]string, spec am.TemplateSpec) bool {
	if len(slots) == 0 {
		return false
	}
	for _, sl := range slots {
		if sl.Blocking || (am.NeedsDecision(sl) && proposal[sl.Key] == "") {
			return false
		}
	}
	return am.CheckTemplateMapping(am.Thai, slots, proposal, spec) == ""
}

// blockingWhy is the first reason the filler gave for refusing the form.
func blockingWhy(blocking []json.RawMessage) string {
	for _, b := range blocking {
		var x struct {
			Reason string `json:"reason"`
			Why    string `json:"why"`
			Label  string `json:"label"`
		}
		if json.Unmarshal(b, &x) == nil {
			if x.Reason != "" {
				return x.Reason
			}
			if x.Why != "" {
				return x.Why
			}
		}
	}
	return ""
}

// formFillJob is a render job that fills the round's form and nothing else
// (the API makes the same job when a person saves the mapping).
func (r *Runner) formFillJob(ctx context.Context, run am.Run) (am.Run, error) {
	params, _ := json.Marshal(map[string]any{"formats": []string{}, "why": "round_form", "form_only": true})
	parent := run.ID
	job, _, err := r.Store.CreateRun(ctx, r.Store.Pool, am.Run{InstallID: run.InstallID, ParentRunID: &parent, TenantID: run.TenantID,
		UserID: run.UserID, Kind: am.KindRender, Params: params, DefinitionVersion: run.DefinitionVersion,
		Checksum: run.Checksum, EngineChecksum: run.EngineChecksum, BindingVersion: run.BindingVersion, ThreadID: run.ThreadID},
		"form-fill:"+run.ID.String()+":"+roundFormKey(ctx, r, run.ID))
	return job, err
}

// fillFormLeftBehind queues the fill for a finished round whose form is mapped and was never filled:
// the reading of a form runs beside the round, and a mapping that became ready after the round's
// render step looked for it would otherwise wait for nobody.
func (r *Runner) fillFormLeftBehind(ctx context.Context, runID uuid.UUID) {
	form, err := r.Store.RoundForm(ctx, r.Store.Pool, runID)
	if err != nil || form == nil || form.Status != am.FormReady || form.Fills > 0 {
		return
	}
	run, err := r.Store.RunByID(ctx, runID)
	if err != nil || run.Status != am.RunSucceeded {
		return
	}
	if _, err := r.formFillJob(ctx, run); err != nil {
		r.Log.Error("a round's form was left unfilled", "run", runID, "err", err)
	}
}

// answersLeftBehind queues the re-render for a finished round whose files were made before an answer
// arrived: read is what its render step read, made the formats it made. An answer about which files
// alone makes only the files the round lacks; any other makes the round's files again. The same
// decision the API takes for an answer given after the round finished (api.go, answer).
func (r *Runner) answersLeftBehind(ctx context.Context, runID uuid.UUID, read map[string]am.Answer, made []string) {
	run, err := r.Store.RunByID(ctx, runID)
	if err != nil || run.Status != am.RunSucceeded || run.Kind != am.KindRun {
		return
	}
	now := map[string]am.Answer{}
	_ = json.Unmarshal(run.Answers, &now)
	late := am.LateAnswers(read, now)
	if len(late) == 0 {
		return
	}
	// answered in the instant between the round finishing and this look: the API saw a finished
	// round and queued the re-render itself
	var queued int
	_ = r.Store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'
		AND params->>'why' IN ('output_set', 'cosmetic_answer') AND created_at >= $2`, runID, run.FinishedAt).Scan(&queued)
	if queued > 0 {
		return
	}
	had, _ := json.Marshal(made)
	formats, only := am.OutputSetAlone(am.Run{Formats: had}, late)
	why := "output_set"
	if !only {
		formats, why = nil, "cosmetic_answer"
	} else if len(formats) == 0 {
		return
	}
	params, _ := json.Marshal(map[string]any{"formats": formats, "why": why})
	b, _ := json.Marshal(late)
	if _, _, err := r.Store.CreateRun(ctx, r.Store.Pool, am.Run{InstallID: run.InstallID, ParentRunID: &runID, TenantID: run.TenantID,
		UserID: run.UserID, Kind: am.KindRender, Params: params, DefinitionVersion: run.DefinitionVersion,
		Checksum: run.Checksum, EngineChecksum: run.EngineChecksum, BindingVersion: run.BindingVersion, ThreadID: run.ThreadID},
		"late-answer:"+am.StepKey(runID, "late-answer", string(b))); err != nil {
		r.Log.Error("a late answer's files were not queued", "run", runID, "err", err)
	}
}

// roundFormKey tells one fill from the next (the mapping changed).
func roundFormKey(ctx context.Context, r *Runner, run uuid.UUID) string {
	f, err := r.Store.RoundForm(ctx, r.Store.Pool, run)
	if err != nil || f == nil {
		return ""
	}
	b, _ := json.Marshal(map[string]any{"sha": f.SHA256, "mapping": f.Mapping, "fills": f.Fills})
	return am.StepKey(run, "form-fill", string(b))
}

// roundFormInput is the round's form as a render input when it is ready to
// be filled (or was, and is filled again after a new mapping), else nil.
func (rc *runCtx) roundFormInput(ctx context.Context, artifactRun uuid.UUID) (*am.TemplateDefault, *am.File, *am.RoundForm, error) {
	if len(rc.pkg.Manifest.Templates) == 0 {
		return nil, nil, nil, nil
	}
	form, err := rc.r.Store.RoundForm(ctx, rc.r.Store.Pool, artifactRun)
	if err != nil && !errors.Is(err, am.ErrNotFound) {
		return nil, nil, nil, err
	}
	if form == nil || (form.Status != am.FormReady && form.Status != am.FormFilled && form.Status != am.FormUnfilled) || len(form.Mapping) == 0 {
		return nil, nil, nil, nil
	}
	f, err := rc.r.Store.GetFile(ctx, rc.run.TenantID, rc.run.UserID, form.FileID)
	if err != nil || f.Status == "removed" {
		return nil, nil, nil, nil
	}
	t := am.TemplateDefault{FileID: form.FileID, Name: form.Name, Kind: form.Kind, SHA256: form.SHA256, Version: form.Fills + 1,
		Slots: form.Slots, Mapping: form.Mapping, SavedAt: form.UpdatedAt}
	return &t, &f, form, nil
}

// recordFormOutcome writes what the fill came to on the round and its item.
func (rc *runCtx) recordFormOutcome(ctx context.Context, artifactRun uuid.UUID, form *am.RoundForm, arts []am.Artifact) error {
	if form == nil {
		return nil
	}
	parent, err := rc.r.Store.RunByID(ctx, artifactRun)
	if err != nil {
		return err
	}
	form.Fills++
	form.Status, form.Unfilled, form.ArtifactID = am.FormFilled, nil, nil
	for _, a := range arts {
		if a.Format != "template" {
			continue
		}
		id := a.ID
		form.ArtifactID = &id
		var q struct {
			Passed   bool              `json:"passed"`
			Unfilled []json.RawMessage `json:"unfilled"`
		}
		if json.Unmarshal(a.Validation, &q) == nil && !q.Passed {
			form.Status, form.Unfilled = am.FormUnfilled, q.Unfilled
		}
	}
	if form.ArtifactID == nil {
		form.Status = am.FormFailed
	}
	if err := rc.r.Store.SyncFormItem(ctx, rc.r.Store.Pool, parent, form); err != nil {
		return err
	}
	return rc.r.Store.SetRoundForm(ctx, rc.r.Store.Pool, artifactRun, form)
}
