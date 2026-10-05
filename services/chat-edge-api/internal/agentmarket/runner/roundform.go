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
// found on the round: ready when every fillable slot has a proposal the
// mapping check accepts — then the fill follows at once if the round has its
// figures, or in its render step — and needs_mapping otherwise, which opens
// the mapping screen on the form's item. The install's saved form is never
// touched.

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
	}
	_ = json.Unmarshal(mustJSON(out), &res)
	form.Slots, form.Proposal, form.ProposedBy = res.Slots, res.Proposal, res.ProposedBy
	var spec am.TemplateSpec
	_ = json.Unmarshal(rc.pkg.Manifest.Templates, &spec)
	switch {
	case len(res.Blocking) > 0:
		form.Status = am.FormFailed
		form.Why = blockingWhy(res.Blocking)
	case formObvious(res.Slots, res.Proposal, spec):
		form.Status, form.Mapping = am.FormReady, res.Proposal
	default:
		form.Status = am.FormNeedsMapping
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

// formObvious: every fillable slot has a proposal the mapping check accepts —
// nothing for a person to decide.
func formObvious(slots []am.TemplateSlot, proposal map[string]string, spec am.TemplateSpec) bool {
	if len(slots) == 0 {
		return false
	}
	for _, sl := range slots {
		if sl.Blocking || (sl.Fillable && proposal[sl.Key] == "") {
			return false
		}
	}
	return am.CheckTemplateMapping(slots, proposal, spec) == ""
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
