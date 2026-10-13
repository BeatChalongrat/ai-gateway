package runner

import (
	"context"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── What a person asks of the documents, beyond the package's list (AM18, part B) ──
//
// The words typed with a round's files are read first now — before a file is read — so that what they
// ask of the documents' language sets the round's (its figures and its package's words follow it since
// AM18 part A), what cannot be done is refused before any work, and what could mean two things is asked
// with a card before the round goes on. What the words ask for from now on is kept as the install's own
// way (its defaults, where a person sees and clears it), and a round starts with that way in force.
//
// A finished round's files keep their language: asked for the other, the agent starts a new round from
// the same files and says so — the old file is not touched, and nothing is translated.

// spec is the package's list with the languages its documents speak.
func (rc *runCtx) spec() am.InstructionSpec {
	m := rc.pkg.Manifest
	if m.Instructions == nil {
		return am.InstructionSpec{}.Speaking(m.Languages)
	}
	return (*m.Instructions).Speaking(m.Languages)
}

// readAsked reads the round's words and acts on what the platform itself does with the reading: the
// language, the choice cards, the standing requests. It returns the context in the round's language, the
// reading, and what the round does next (actPark while a choice card waits, actNone to go on).
func (rc *runCtx) readAsked(ctx context.Context) (context.Context, am.Asked, int, error) {
	if rc.pkg.Manifest.UsesWeb() && !rc.docsMayLeave && rc.run.Kind != am.KindSelfTest {
		// AM12: an agent that works on the web whose switch is off does nothing with the request — not even
		// read it; the round stops at its brief (webBrief: web_closed) as it did before the words were read first
		return ctx, am.Asked{}, actNone, nil
	}
	asked, err := rc.asked(ctx, rc.run.Message, rc.pkg.Manifest.FromRequest())
	if err != nil {
		return ctx, asked, actNone, err
	}
	spec := rc.spec()
	// what a person chose on a card asked earlier in this round, for a request that could mean two things
	for _, id := range am.AskedChoices(rc.answers) {
		if l := am.LangOfID(id); l != "" {
			asked.Lang = string(l)
			continue
		}
		asked.Apply = spec.Merge(asked.Apply, []string{id})
	}
	// what still could mean two things is asked, with the card every other decision uses, before any work
	var findings []am.Finding
	for n, a := range asked.Ask {
		if _, done := rc.answers[am.AskedChoiceKey(n)]; done {
			continue
		}
		if f := am.AskedChoiceFinding(am.LangOf(ctx), spec, n, a); f != nil {
			findings = append(findings, *f)
		}
	}
	if len(findings) > 0 {
		act, err := rc.clarify(ctx, "preflight", findings, nil, "", preflightOut{})
		if err != nil || act != actNone {
			return ctx, asked, act, err
		}
	}
	if asked.Lang == "" && rc.run.Kind == am.KindRun {
		// the install's own language — asked for from now on, or set on its settings page — is the
		// round's when the words say nothing; the words win when they do
		if defs, err := rc.r.Store.ListDefaults(ctx, rc.install.ID); err == nil {
			if _, l := am.StandingOf(defs); l != "" {
				asked.Lang = string(l)
			}
		}
	}
	if ctx, err = rc.takeLanguage(ctx, asked); err != nil {
		return ctx, asked, actNone, err
	}
	rc.keepStanding(ctx, asked, spec)
	rc.standing = rc.standingNow(ctx, spec)
	rc.instr = spec.Merge(rc.standing, asked.Apply)
	return ctx, asked, actNone, nil
}

// takeLanguage makes the round's language the one its words ask for: written on the run, and the context
// every step after this is in. A job done for a run keeps its parent's language and is never switched.
func (rc *runCtx) takeLanguage(ctx context.Context, asked am.Asked) (context.Context, error) {
	l := am.Lang(asked.Lang)
	if l == "" || rc.run.Kind != am.KindRun || l == rc.run.Lang() {
		return ctx, nil
	}
	if err := rc.r.Store.SetRunLang(ctx, rc.run.ID, l); err != nil {
		return ctx, err
	}
	run, err := rc.r.Store.RunByID(ctx, rc.run.ID)
	if err != nil {
		return ctx, err
	}
	rc.run = run
	ctx = am.WithLang(ctx, l)
	rc.ctx = ctx
	return ctx, nil
}

// keepStanding records what the words asked for from now on as the install's own way: the package
// directives under StandingKey (merged with those kept before, one of a group), the language under
// LanguageKey. Nothing is kept by a self-test.
func (rc *runCtx) keepStanding(ctx context.Context, asked am.Asked, spec am.InstructionSpec) {
	if len(asked.Standing) == 0 || rc.run.Kind == am.KindSelfTest {
		return
	}
	var ids []string
	var lang am.Lang
	for _, id := range asked.Standing {
		if l := am.LangOfID(id); l != "" {
			lang = l
			continue
		}
		ids = append(ids, id)
	}
	st := rc.r.Store
	rid := rc.run.ID
	if len(ids) > 0 {
		defs, _ := st.ListDefaults(ctx, rc.install.ID)
		have, _ := am.StandingOf(defs)
		if err := st.SetDefault(ctx, st.Pool, rc.install.ID, am.StandingDefault(spec, spec.Merge(have, ids), &rid)); err != nil {
			rc.log.Warn("standing instructions not kept", "err", err)
		}
	}
	if lang != "" {
		if err := st.SetDefault(ctx, st.Pool, rc.install.ID, am.LanguageDefault(lang, &rid)); err != nil {
			rc.log.Warn("standing language not kept", "err", err)
		}
	}
}

// standingNow: the package directives the install keeps as its own way, those the package still declares.
func (rc *runCtx) standingNow(ctx context.Context, spec am.InstructionSpec) []string {
	defs, err := rc.r.Store.ListDefaults(ctx, rc.install.ID)
	if err != nil {
		return nil
	}
	ids, _ := am.StandingOf(defs)
	var out []string
	for _, id := range ids {
		for _, d := range spec.Can {
			if d.ID == id {
				out = append(out, id)
			}
		}
	}
	return out
}

// moreAsked is what the thread says of the platform's own part of the reading: the language the round
// was made in when it was asked, what was kept from now on and what was for this round only, and what
// was done because it was kept earlier.
func (rc *runCtx) moreAsked(asked am.Asked, done renderAsked) []am.PersonText {
	spec := rc.spec()
	var lines []am.PersonText
	if l := am.Lang(asked.Lang); l != "" && rc.run.Kind == am.KindRun {
		lines = append(lines, am.AskedLanguage(l))
	}
	if len(asked.Standing) > 0 {
		if said := am.AskedStanding(spec, asked.Standing); !said.EmptyIn(am.Thai) {
			lines = append(lines, said)
		}
		var once []string
		for _, id := range asked.Apply {
			if !contains(asked.Standing, id) && contains(done.Applied, id) {
				once = append(once, id)
			}
		}
		if said := am.AskedOnce(spec, once); !said.EmptyIn(am.Thai) {
			lines = append(lines, said)
		}
	}
	var kept []string
	for _, id := range rc.standing {
		if !contains(asked.Apply, id) && !contains(asked.Standing, id) && contains(done.Applied, id) {
			kept = append(kept, id)
		}
	}
	if said := am.AskedByStanding(spec, kept); !said.EmptyIn(am.Thai) {
		lines = append(lines, said)
	}
	return lines
}

// newRoundInLanguage starts a round from a finished round's files, in the language asked: the same
// files, the same words (the new round reads them itself), the same formats and model, in the same chat.
func (r *Runner) newRoundInLanguage(ctx context.Context, parent am.Run, text string, l am.Lang) error {
	st := r.Store
	files, err := st.RunFiles(ctx, parent.ID, "active")
	if err != nil {
		return err
	}
	run, _, err := st.CreateRun(am.WithLang(ctx, l), st.Pool, am.Run{InstallID: parent.InstallID, TenantID: parent.TenantID,
		UserID: parent.UserID, Kind: am.KindRun, Message: text, DefinitionVersion: parent.DefinitionVersion,
		Checksum: parent.Checksum, EngineChecksum: parent.EngineChecksum, BindingVersion: parent.BindingVersion,
		Formats: parent.Formats, ThreadID: parent.ThreadID, ModelChoice: parent.ModelChoice}, "")
	if err != nil {
		return err
	}
	// (a file belongs to one run, and a run's objects go when the run is purged: the new round gets
	// copies of its own, under keys of its own)
	for _, f := range files {
		body, _, _, err := r.Files.Download(ctx, f.StorageKey)
		if err != nil {
			return err
		}
		id := uuid.New()
		key := am.UploadKey(f.TenantID, f.InstallID, id, f.Name)
		err = r.Files.Upload(ctx, key, body, f.ContentType, f.Size)
		body.Close()
		if err != nil {
			return err
		}
		rid := run.ID
		if _, err := st.CreateFile(ctx, st.Pool, am.File{ID: id, TenantID: f.TenantID, UserID: f.UserID, InstallID: f.InstallID, RunID: &rid,
			Name: f.Name, ContentType: f.ContentType, Size: f.Size, SHA256: f.SHA256, StorageKey: key, Status: "active"}); err != nil {
			return err
		}
	}
	rid := run.ID
	_, _, err = st.AddItem(am.WithLang(ctx, l), st.Pool, run.InstallID, run.TenantID, run.UserID, &rid, am.QueuedProgress(run), nil)
	return err
}
