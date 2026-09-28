package agentmarket

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── Sign-off (AM3) ───────────────────────────────────────────────────────
//
// A financial document that goes into a real close needs a person's name on
// it, not "an AI made this". A reviewer — the run's owner, or a colleague it
// was shared with "may review" — marks a finished run reviewed; that is
// recorded (run_reviews, and a line in the run's thread), and a job (kind
// review) re-issues the run's files with the reviewer's name, the time and
// the note on their face: a new version of each file, the unsigned version
// kept. Nothing is written back anywhere and nothing is sent.

// Review is one sign-off.
type Review struct {
	ID               uuid.UUID       `json:"id"`
	RunID            uuid.UUID       `json:"run_id"`
	ReviewerEmail    string          `json:"reviewer_email"`
	ReviewerName     string          `json:"reviewer_name"`
	Note             string          `json:"note"`
	ViaShareID       *uuid.UUID      `json:"via_share_id,omitempty"`
	StampRunID       *uuid.UUID      `json:"stamp_run_id,omitempty"`
	ArtifactVersions json.RawMessage `json:"artifact_versions"`
	CreatedAt        time.Time       `json:"created_at"`
	StampStatus      string          `json:"stamp_status,omitempty"`
}

const reviewCols = `v.id, v.run_id, v.reviewer_email, v.reviewer_name, v.note, v.via_share_id, v.stamp_run_id, v.artifact_versions, v.created_at,
	COALESCE(j.status, '')`

func scanReview(r pgx.Row) (Review, error) {
	var x Review
	err := r.Scan(&x.ID, &x.RunID, &x.ReviewerEmail, &x.ReviewerName, &x.Note, &x.ViaShareID, &x.StampRunID, &x.ArtifactVersions,
		&x.CreatedAt, &x.StampStatus)
	return x, err
}

// RunReviews lists a run's sign-offs, oldest first.
func (s *Store) RunReviews(ctx context.Context, run uuid.UUID) ([]Review, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+reviewCols+` FROM agent_market.run_reviews v
		LEFT JOIN agent_market.agent_runs j ON j.id = v.stamp_run_id WHERE v.run_id = $1 ORDER BY v.created_at`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Review
	for rows.Next() {
		x, err := scanReview(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

var thaiMonthsShort = []string{"ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."}

// ThaiDateTime is how a sign-off's time reads on a document ("28 ก.ย. 2569 เวลา 10:05 น.", Bangkok time).
func ThaiDateTime(t time.Time) string {
	loc, err := time.LoadLocation("Asia/Bangkok")
	if err != nil {
		loc = time.FixedZone("ICT", 7*3600)
	}
	t = t.In(loc)
	return fmt.Sprintf("%d %s %d เวลา %02d:%02d น.", t.Day(), thaiMonthsShort[int(t.Month())-1], t.Year()+543, t.Hour(), t.Minute())
}

type reviewReq struct {
	Name string `json:"name"`
	Note string `json:"note"`
}

// signOff records a review of a finished run and queues the stamp job.
func (a *API) signOff(ctx context.Context, w http.ResponseWriter, r *http.Request, run Run, via *uuid.UUID) {
	c, _ := caller(r)
	acc := accessOf(r)
	var body reviewReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	if run.Kind == KindRun && (run.Status == RunFailed || run.Status == RunCancelled) {
		// AM5: a round that ended without succeeding (a reconciliation that does
		// not tie) is not "still running" — waiting will not make it reviewable
		say(w, http.StatusConflict, "NOT_REVIEWABLE", "why", "รอบนี้ไม่สำเร็จ ไฟล์ของรอบนี้เป็นร่าง",
			"next", "แก้สาเหตุแล้วเริ่มรอบใหม่ให้สำเร็จก่อน แล้วค่อยตรวจทานรอบนั้น")
		return
	}
	if run.Kind != KindRun || run.Status != RunSucceeded {
		say(w, http.StatusConflict, "NOT_REVIEWABLE", "why", "รอบนี้ยังทำไม่เสร็จ", "next", "รอให้รอบนี้เสร็จก่อน แล้วค่อยตรวจทาน")
		return
	}
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	// AM5: an agent may ask that its work is signed by someone who did not
	// prepare it (a bank reconciliation). Who a person is is who is signed
	// in — the name typed on the form is only what the stamp prints.
	if def, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
		if m, err := ParseManifest(def.Manifest); err == nil && m.Review.SeparateReviewer &&
			(c.UserID == run.UserID || c.UserID == in.UserID) {
			say(w, http.StatusConflict, "SAME_PERSON")
			return
		}
	}
	var open int
	if err := a.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_run_exceptions WHERE run_id = $1 AND status = 'open'`,
		run.ID).Scan(&open); err != nil {
		a.fail(w, err, "run")
		return
	}
	if open > 0 {
		say(w, http.StatusConflict, "NOT_REVIEWABLE", "why", fmt.Sprintf("ยังมีข้อยกเว้นที่รอคนตัดสิน %d รายการ", open),
			"next", "ตัดสินข้อยกเว้นให้ครบก่อน แล้วค่อยตรวจทาน")
		return
	}
	arts, err := a.store.RunArtifacts(ctx, run.ID)
	if err != nil {
		a.fail(w, err, "artifact")
		return
	}
	if len(arts) == 0 {
		say(w, http.StatusConflict, "NOT_REVIEWABLE", "why", "รอบนี้ยังไม่มีไฟล์", "next", "ขอไฟล์จาก agent ก่อน แล้วค่อยตรวจทาน")
		return
	}
	name := strings.Join(strings.Fields(body.Name), " ")
	if name == "" {
		name = a.store.DisplayName(ctx, acc.OrgID, c.UserID, c.Email)
	}
	if r := []rune(name); len(r) > 120 {
		name = string(r[:120])
	}
	note := strings.TrimSpace(body.Note)
	if r := []rune(note); len(r) > 500 {
		note = string(r[:500])
	}
	now := time.Now()
	versions := map[string]int{}
	for _, x := range latestArtifacts(arts) {
		versions[x.Format] = x.Version
	}
	vb, _ := json.Marshal(versions)
	preparer := a.store.DisplayName(ctx, in.OrgID, in.UserID, "")
	agent := in.Title
	if d, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
		agent = d.Name
	}
	agent += " รุ่น " + run.DefinitionVersion
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var rev Review
	if err := tx.QueryRow(ctx, `INSERT INTO agent_market.run_reviews (run_id, org_id, reviewer_user_id, reviewer_email, reviewer_name,
		note, via_share_id, artifact_versions) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		RETURNING id, run_id, reviewer_email, reviewer_name, note, via_share_id, stamp_run_id, artifact_versions, created_at`,
		run.ID, orgOr(in.OrgID), c.UserID, normEmail(c.Email), name, note, via, vb).
		Scan(&rev.ID, &rev.RunID, &rev.ReviewerEmail, &rev.ReviewerName, &rev.Note, &rev.ViaShareID, &rev.StampRunID,
			&rev.ArtifactVersions, &rev.CreatedAt); err != nil {
		a.fail(w, err, "run")
		return
	}
	params, _ := json.Marshal(map[string]any{"review_id": rev.ID, "reviewer": name, "reviewer_email": normEmail(c.Email),
		"at_label": ThaiDateTime(now), "note": note, "preparer": preparer, "agent": agent, "run_ref": run.ID.String()[:8]})
	parent := run.ID
	job, _, err := a.store.CreateRun(ctx, tx, Run{InstallID: run.InstallID, ParentRunID: &parent, TenantID: run.TenantID,
		UserID: run.UserID, Kind: KindReview, Params: params, DefinitionVersion: run.DefinitionVersion, Checksum: run.Checksum,
		EngineChecksum: run.EngineChecksum, BindingVersion: run.BindingVersion}, "")
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.run_reviews SET stamp_run_id = $2 WHERE id = $1`, rev.ID, job.ID); err != nil {
		a.fail(w, err, "run")
		return
	}
	rev.StampRunID = &job.ID
	text := "ตรวจทานแล้วโดย " + name + " · " + ThaiDateTime(now)
	if note != "" {
		text += " · หมายเหตุ: " + note
	}
	text += " — กำลังใส่ชื่อผู้ตรวจทานลงในไฟล์ ไฟล์ฉบับก่อนตรวจทานยังเก็บไว้"
	rid := run.ID
	if _, _, err := a.store.AddItem(ctx, tx, run.InstallID, run.TenantID, run.UserID, &rid, "notice",
		map[string]any{"text": text, "run_id": run.ID.String(), "review_id": rev.ID.String(), "tone": "success"}, nil); err != nil {
		a.fail(w, err, "run")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "run")
		return
	}
	rev.StampStatus = RunQueued
	writeJSON(w, http.StatusOK, map[string]any{"review": rev, "job": job})
}

func (a *API) review(w http.ResponseWriter, r *http.Request) {
	run, ok := a.ownedRun(w, r)
	if !ok {
		return
	}
	a.signOff(r.Context(), w, r, run, nil)
}

func (a *API) reviews(w http.ResponseWriter, r *http.Request) {
	run, ok := a.ownedRun(w, r)
	if !ok {
		return
	}
	list, err := a.store.RunReviews(r.Context(), run.ID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"reviews": nonNil(list)})
}

func (a *API) sharedReview(w http.ResponseWriter, r *http.Request) {
	sh, run, ok := a.openShare(w, r)
	if !ok {
		return
	}
	if !sh.CanReview {
		say(w, http.StatusForbidden, "CANNOT_REVIEW")
		return
	}
	id := sh.ID
	a.signOff(r.Context(), w, r, run, &id)
}
